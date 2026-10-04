"""Bounded OpenAI-compatible QianWen adapter. No SDK retries, automatic model switch or tools."""

import hashlib
import json
import math
import re
import unicodedata
from collections import OrderedDict
from decimal import Decimal
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import ValidationError

from deephelp_app.adapters.providers import EndpointConfig, ProviderConfig
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.execution import AsyncCalls, ExecutionBudget
from deephelp_app.domain.models import (
    ChatMessage,
    ChatRequest,
    ChatResult,
    EmbeddingResult,
    ErrorCode,
    ModelToolCall,
    ModelUsage,
)


def diagnostic_usage(value: object) -> dict[str, int | None]:
    """Only API-provided counts; absent or malformed counts stay unknown."""
    raw = value if isinstance(value, dict) else {}
    details = raw.get("completion_tokens_details")
    details = details if isinstance(details, dict) else {}
    counts = {
        "input_tokens": raw.get("prompt_tokens"),
        "output_tokens": raw.get("completion_tokens"),
        "total_tokens": raw.get("total_tokens"),
        "reasoning_tokens": details.get("reasoning_tokens"),
    }
    return {key: n if type(n) is int and n >= 0 else None for key, n in counts.items()}


def safe_identifier(value: object) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value):
        return value
    return None


def validate_schema(schema: dict[str, object]) -> Draft202012Validator:
    """Schemas are local only: remote references must never fetch untrusted URLs."""

    def check_refs(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef"} and (
                    not isinstance(item, str) or not item.startswith("#")
                ):
                    raise AppError(
                        ErrorCode.INVALID_ARGUMENT, "External schema references disabled"
                    )
                check_refs(item)
        elif isinstance(value, list):
            for item in value:
                check_refs(item)

    check_refs(schema)
    try:
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)
    except SchemaError:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "Invalid local JSON schema") from None


def invalid_output(request_id: str | None = None) -> AppError:
    return AppError(
        ErrorCode.MODEL_OUTPUT_INVALID,
        "Model response failed validation",
        provider_request_id=request_id,
    )


class QianwenGateway:
    def __init__(
        self,
        client: httpx.AsyncClient,
        calls: AsyncCalls,
        config: ProviderConfig,
        chat_credentials: tuple[str, str],
        embedding_credentials: tuple[str, str],
    ) -> None:
        self.client, self.calls, self.config = client, calls, config
        self._chat_credentials = chat_credentials
        self._embedding_credentials = embedding_credentials
        self.signature = config.signature()
        if config.allow_zero_embedding_indices and (
            config.provider != "qianwen" or config.embedding.model != "qwen3.7-text-embedding-flash"
        ):
            raise ConfigurationError("Unverified positional embedding adapter")
        self._cache: OrderedDict[str, tuple[tuple[float, ...], int, bool]] = OrderedDict()
        self._cache_bytes = 0
        # Structural diagnostics only. No keys, URLs, prompts, content or arguments.
        self.diagnostics: list[dict[str, object]] = []

    def _diagnose(self, item: dict[str, object]) -> None:
        self.diagnostics.append(item)
        del self.diagnostics[:-100]

    def record_diagnostics(self, path: Path) -> None:
        """A bounded structural cassette; semantic responses remain synthetic fixtures."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"format": "m03-structure-v1", "records": self.diagnostics},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _cost(self, endpoint: EndpointConfig, usage: ModelUsage) -> Decimal:
        return (
            endpoint.input_cny_per_million * usage.input_tokens
            + endpoint.output_cny_per_million * usage.output_tokens
        ) / 1_000_000

    async def _post(
        self,
        path: str,
        body: dict[str, object],
        endpoint: EndpointConfig,
        credentials: tuple[str, str],
        budget: ExecutionBudget,
        max_output: int = 0,
    ) -> tuple[dict[str, object], ModelUsage, str | None]:
        if budget.token_limit is None or budget.cost_limit is None:
            raise ConfigurationError("Model calls require explicit token and cost limits")
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        if len(encoded) > self.config.max_request_bytes:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Model request exceeds byte limit")
        # UTF-8 byte count plus framing allowance deliberately overestimates these bounded
        # text-only prompts; explicit thinking allowance is included by the caller.
        # Search and SDK retries are disabled. Usage overruns fail closed.
        reserved_tokens = len(encoded) + 1024 + max_output
        reserved_cost = self._cost(
            endpoint,
            ModelUsage(
                input_tokens=len(encoded) + 1024,
                output_tokens=max_output,
            ),
        )

        async def operation() -> tuple[dict[str, object], ModelUsage, str | None]:
            base, key = credentials
            async with self.client.stream(
                "POST",
                base + "/" + path,
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                content=encoded,
            ) as response:
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(chunks) + len(chunk) > self.config.max_response_bytes:
                        raise invalid_output(safe_identifier(response.headers.get("x-request-id")))
                    chunks.extend(chunk)
                try:
                    data = json.loads(chunks)
                    if not isinstance(data, dict):
                        raise ValueError
                except ValueError, UnicodeDecodeError:
                    if 200 <= response.status_code < 300:
                        raise invalid_output(
                            safe_identifier(response.headers.get("x-request-id"))
                        ) from None
                    data = {}
                request_id = safe_identifier(
                    response.headers.get("x-request-id")
                ) or safe_identifier(data.get("id"))
                self._diagnose(
                    {
                        "path": path,
                        "model": endpoint.model,
                        "http_status": response.status_code,
                        "provider_request_id": request_id,
                        "api_usage": diagnostic_usage(data.get("usage")),
                    }
                )
                if not 200 <= response.status_code < 300:
                    error = data.get("error", {})
                    code = safe_identifier(error.get("code")) if isinstance(error, dict) else None
                    lowered = (code or "").lower()
                    if any(marker in lowered for marker in ("quota", "balance", "arrearage")):
                        kind = ErrorCode.PROVIDER_QUOTA_EXHAUSTED
                    elif response.status_code == 401:
                        kind = ErrorCode.UNAUTHENTICATED
                    elif response.status_code == 403:
                        kind = ErrorCode.FORBIDDEN
                    elif response.status_code == 429:
                        kind = ErrorCode.RATE_LIMITED
                    elif response.status_code >= 500:
                        kind = ErrorCode.UPSTREAM_UNAVAILABLE
                    else:
                        kind = ErrorCode.MODEL_CAPABILITY_UNAVAILABLE
                    raise AppError(
                        kind,
                        "Provider request rejected",
                        retryable=kind in {ErrorCode.RATE_LIMITED, ErrorCode.UPSTREAM_UNAVAILABLE},
                        provider_request_id=request_id,
                        provider_code=code,
                    )
                try:
                    raw_usage = data["usage"]
                    if not isinstance(raw_usage, dict):
                        raise ValueError
                    usage = ModelUsage.model_validate(
                        {
                            "input_tokens": raw_usage.get(
                                "prompt_tokens", raw_usage.get("total_tokens")
                            ),
                            "output_tokens": raw_usage.get("completion_tokens", 0),
                        }
                    )
                    if (
                        type(raw_usage.get("total_tokens")) is not int
                        or raw_usage.get("total_tokens") != usage.total_tokens
                    ):
                        raise ValueError
                except KeyError, ValueError, ValidationError:
                    raise invalid_output(request_id) from None
                budget.settle_model(
                    reserved_tokens, reserved_cost, usage.total_tokens, self._cost(endpoint, usage)
                )
                if data.get("model") != endpoint.model:
                    raise invalid_output(request_id)
                return data, usage, request_id

        return await self.calls.call(
            operation,
            budget,
            retry_safe=True,
            token_reservation=reserved_tokens,
            cost_reservation=reserved_cost,
        )

    async def complete(self, text: str, budget: ExecutionBudget) -> str:
        result = await self.chat(
            ChatRequest(messages=[ChatMessage(role="user", content=text)]), budget
        )
        if result.content is None or result.tool_calls:
            raise invalid_output(result.provider_request_id)
        return result.content

    async def _chat_once(self, request: ChatRequest, budget: ExecutionBudget) -> ChatResult:
        messages: list[dict[str, object]] = []
        for message in request.messages:
            item: dict[str, object] = {"role": message.role, "content": message.content}
            if message.tool_call_id:
                item["tool_call_id"] = message.tool_call_id
            if message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in message.tool_calls
                ]
            messages.append(item)
        thinking = (
            self.config.enable_thinking
            if request.enable_thinking is None
            else request.enable_thinking
        )
        thinking_allowance = (
            (request.thinking_budget or self.config.thinking_budget) if thinking else 0
        )
        if request.thinking_budget is not None and not thinking:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Thinking budget requires thinking mode")
        body: dict[str, object] = {
            "model": self.config.chat.model,
            "messages": messages,
            "max_tokens": request.max_output_tokens,
            "enable_thinking": thinking,
            "temperature": 0,
            "stream": False,
        }
        if thinking:
            body["thinking_budget"] = thinking_allowance
        if request.response_format == "json_schema":
            if not self.config.strict_schema:
                raise AppError(
                    ErrorCode.MODEL_CAPABILITY_UNAVAILABLE, "Strict schema not configured"
                )
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "deephelp_result",
                    "strict": True,
                    "schema": request.output_schema,
                },
            }
        elif request.response_format == "json_object":
            body["response_format"] = {"type": "json_object"}
        if request.tools:
            if not self.config.tool_calling:
                raise AppError(ErrorCode.MODEL_CAPABILITY_UNAVAILABLE, "Tools not configured")
            body["tools"] = [
                {"type": "function", "function": tool.model_dump()} for tool in request.tools
            ]
            if request.tool_choice:
                body["tool_choice"] = {
                    "type": "function",
                    "function": {"name": request.tool_choice},
                }
        data, usage, request_id = await self._post(
            "chat/completions",
            body,
            self.config.chat,
            self._chat_credentials,
            budget,
            request.max_output_tokens + thinking_allowance,
        )
        try:
            choices = data["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError
            choice = choices[0]
            message = choice["message"]
            if message.get("role") != "assistant":
                raise ValueError
            tools = {tool.name: tool for tool in request.tools}
            tool_calls = []
            for call in message.get("tool_calls", []):
                function = call["function"]
                if call["type"] != "function" or function["name"] not in tools:
                    raise ValueError
                arguments = json.loads(function["arguments"])
                if not isinstance(arguments, dict):
                    raise ValueError
                if not validate_schema(tools[function["name"]].parameters).is_valid(arguments):
                    raise ValueError
                tool_calls.append(
                    ModelToolCall(call_id=call["id"], name=function["name"], arguments=arguments)
                )
            if len({call.call_id for call in tool_calls}) != len(tool_calls):
                raise ValueError
            if request.tool_choice and (
                len(tool_calls) != 1 or tool_calls[0].name != request.tool_choice
            ):
                raise ValueError
            result = ChatResult(
                content=message.get("content"),
                tool_calls=tool_calls,
                usage=usage,
                finish_reason=choice["finish_reason"],
                provider_request_id=request_id,
                model=self.config.chat.model,
            )
            if (not result.content and not tool_calls) or result.finish_reason not in {
                "stop",
                "tool_calls",
                "length",
            }:
                raise ValueError
            self._diagnose(
                {
                    "capability": "chat",
                    "provider_request_id": request_id,
                    "usage": usage.model_dump(),
                    "finish_reason": result.finish_reason,
                    "tool_names": [call.name for call in tool_calls],
                    "content_length": len(result.content or ""),
                    "thinking_enabled": thinking,
                    "reasoning_content_length": len(message.get("reasoning_content") or ""),
                }
            )
            return result
        except ValueError, TypeError, KeyError, ValidationError:
            raise invalid_output(request_id) from None

    async def chat(self, request: ChatRequest, budget: ExecutionBudget) -> ChatResult:
        validator = (
            validate_schema(request.output_schema) if request.output_schema is not None else None
        )
        for tool in request.tools:
            validate_schema(tool.parameters)
        result = await self._chat_once(request, budget)
        first_usage = result.usage
        for repair in range(2 if request.repair_once and validator is not None else 1):
            try:
                if result.finish_reason == "length":
                    raise ValueError
                if validator is None:
                    return result
                parsed = json.loads(result.content or "")
                if not isinstance(parsed, dict) or not validator.is_valid(parsed):
                    raise ValueError
                return result.model_copy(update={"structured": parsed})
            except ValueError, TypeError:
                if repair or not request.repair_once or validator is None:
                    raise invalid_output(result.provider_request_id) from None
                correction = ChatMessage(
                    role="system", content="Return only valid JSON matching the schema."
                )
                repaired = request.model_copy(
                    update={"messages": [*request.messages, correction], "repair_once": False}
                )
                result = await self._chat_once(repaired, budget)
                result = result.model_copy(
                    update={
                        "usage": ModelUsage(
                            input_tokens=first_usage.input_tokens + result.usage.input_tokens,
                            output_tokens=first_usage.output_tokens + result.usage.output_tokens,
                        )
                    }
                )
        raise invalid_output()  # unreachable defensive boundary

    def _cache_key(self, text: str) -> str:
        return hashlib.sha256((self.signature.fingerprint + "\0" + text).encode()).hexdigest()

    async def embed(self, texts: list[str], budget: ExecutionBudget) -> EmbeddingResult:
        budget.remaining_seconds()
        if not texts or len(texts) > self.config.max_texts:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Embedding input count outside limit")
        normalized = []
        for text in texts:
            if not isinstance(text, str):
                raise AppError(ErrorCode.INVALID_ARGUMENT, "Embedding texts must be strings")
            value = unicodedata.normalize("NFC", text).strip()
            if not value or len(value.encode()) > self.config.max_request_bytes // 2:
                raise AppError(ErrorCode.INVALID_ARGUMENT, "Embedding text empty or oversized")
            normalized.append(value)
        found: dict[str, tuple[float, ...]] = {}
        fallback = False
        pending = []
        for text in dict.fromkeys(normalized):
            key = self._cache_key(text)
            if key in self._cache:
                vector, _, positional = self._cache[key]
                found[text] = vector
                fallback |= positional
                self._cache.move_to_end(key)
            else:
                pending.append(text)
        cache_hits = len(normalized) - len(pending)
        input_tokens = 0
        request_ids: list[str] = []
        for start in range(0, len(pending), self.config.batch_size):
            batch = pending[start : start + self.config.batch_size]
            body: dict[str, object] = {
                "model": self.config.embedding.model,
                "input": batch,
                "dimensions": self.signature.dimension,
                "encoding_format": "float",
            }
            data, usage, request_id = await self._post(
                "embeddings",
                body,
                self.config.embedding,
                self._embedding_credentials,
                budget,
            )
            input_tokens += usage.input_tokens
            if request_id:
                request_ids.append(request_id)
            try:
                rows = data["data"]
                if not isinstance(rows, list) or len(rows) != len(batch):
                    raise ValueError
                indices = [row["index"] for row in rows]
                self._diagnose(
                    {
                        "capability": "embedding_structure",
                        "provider_request_id": request_id,
                        "indices": [i if type(i) is int else None for i in indices],
                        "dimensions": [
                            len(row.get("embedding", []))
                            if isinstance(row.get("embedding"), list)
                            else None
                            for row in rows
                        ],
                    }
                )
                if any(type(index) is not int for index in indices):
                    raise ValueError
                positional = False
                if sorted(indices) == list(range(len(batch))):
                    rows = sorted(rows, key=lambda row: row["index"])
                elif self.config.allow_zero_embedding_indices and indices == [0] * len(batch):
                    positional = True
                else:
                    raise ValueError
                parsed_vectors = []
                for row in rows:
                    raw = row["embedding"]
                    if (
                        not isinstance(raw, list)
                        or len(raw) != self.signature.dimension
                        or any(type(v) not in {int, float} or not math.isfinite(v) for v in raw)
                    ):
                        raise ValueError
                    norm = math.hypot(*raw)
                    if not math.isfinite(norm) or norm == 0:
                        raise ValueError
                    parsed_vectors.append(
                        tuple(
                            float(v / norm if self.signature.normalization == "l2" else v)
                            for v in raw
                        )
                    )
            except ValueError, TypeError, KeyError:
                self._diagnose(
                    {
                        "capability": "embedding",
                        "provider_request_id": request_id,
                        "validation": "failed",
                        "expected_dimension": self.signature.dimension,
                    }
                )
                raise invalid_output(request_id) from None
            fallback |= positional
            self._diagnose(
                {
                    "capability": "embedding",
                    "provider_request_id": request_id,
                    "indices": indices,
                    "rows": len(rows),
                    "dimension": self.signature.dimension,
                    "finite": True,
                    "position_index_fallback": positional,
                    "usage": usage.model_dump(),
                    "signature": self.signature.fingerprint,
                }
            )
            for text, vector in zip(batch, parsed_vectors, strict=True):
                found[text] = vector
                key = self._cache_key(text)
                size = len(vector) * 32 + len(text.encode()) + 256
                if key in self._cache:
                    self._cache_bytes -= self._cache.pop(key)[1]
                if self.config.cache_entries and size <= self.config.cache_bytes:
                    self._cache[key] = (vector, size, positional)
                    self._cache_bytes += size
                    while (
                        len(self._cache) > self.config.cache_entries
                        or self._cache_bytes > self.config.cache_bytes
                    ):
                        _, (_, removed_size, _) = self._cache.popitem(last=False)
                        self._cache_bytes -= removed_size
        return EmbeddingResult(
            vectors=[list(found[text]) for text in normalized],
            signature=self.signature,
            usage=ModelUsage(input_tokens=input_tokens),
            cache_hits=cache_hits,
            provider_request_ids=request_ids,
            position_index_fallback=fallback,
        )

"""Original retrieval Ports over bounded, identity-bound official HTTP MCP calls."""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal, cast
from uuid import uuid4

import httpx2
from mcp.types import RequestParamsMeta, TextContent, TextResourceContents
from pydantic import ValidationError

from deephelp_app.adapters.mcp.business.mcp_protocol import canonical
from deephelp_app.adapters.mcp.retrieval.retrieval_protocol import (
    HEALTH_URI,
    NAMES,
    RetrievalBinding,
    registered_tools,
    validate_results,
)
from deephelp_app.adapters.mcp.shared.network_mcp import (
    DelegationVerifier,
    HTTPMCPConfig,
    NetworkMCPClient,
)
from deephelp_app.adapters.observability.telemetry import observed
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.execution import ExecutionBudget, request_execution
from deephelp_app.domain.models.common import ErrorCode, ErrorDetail
from deephelp_app.domain.models.retrieval import DenseResult, DenseScope
from deephelp_app.domain.models.retrieval_protocol import (
    RetrievalContext,
    RetrievalDelegation,
    RetrievalQuery,
    RetrievalWire,
)


class RemoteRetriever:
    def __init__(
        self,
        config: HTTPMCPConfig,
        binding: RetrievalBinding,
        *,
        on_record: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if config.audience != "retrieval" or not binding.records:
            raise ConfigurationError(
                "Remote retrieval needs owned corpus and retrieval credentials"
            )
        self.config, self.binding = config, binding
        self.credentials = config.credentials()
        self.network = NetworkMCPClient(config, self.credentials)
        self.receipts = DelegationVerifier(self.credentials, "retrieval-receipt")
        self.on_record = on_record
        self.diagnostics: list[dict[str, Any]] = []
        self.entered, self.connected = False, False
        self.pid: int | None = None

    @asynccontextmanager
    async def open(self) -> AsyncIterator[RemoteRetriever]:
        if self.entered:
            raise ConfigurationError("Remote retrieval lifespan is single-use")
        self.entered = True
        async with asyncio.timeout(30):
            tools = await self.network.list_tools()
            expected = {t.name: t for t in registered_tools(self.binding)}
            if (
                tools.next_cursor is not None
                or len(tools.tools) != len(expected)
                or any(t.name not in expected or t != expected[t.name] for t in tools.tools)
                or {t.name for t in tools.tools} != set(expected)
            ):
                raise AppError(
                    ErrorCode.MODEL_CAPABILITY_UNAVAILABLE, "Retrieval schema/version differs"
                )
            meta = self.credentials.protect(
                "retrieval",
                {
                    "action": "read_resource",
                    "uri": HEALTH_URI,
                    "nonce": uuid4().hex,
                },
            )
            resource = await self.network.read_resource(HEALTH_URI, meta=meta, cache_mode="bypass")
            if len(resource.contents) != 1 or not isinstance(
                resource.contents[0], TextResourceContents
            ):
                raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Invalid retrieval health resource")
            data = json.loads(resource.contents[0].text)
            if (
                not isinstance(data, dict)
                or type(data.get("pid")) is not int
                or data["pid"] <= 0
                or {k: v for k, v in data.items() if k != "pid"} != self.binding.health()
            ):
                raise AppError(ErrorCode.VERSION_CONFLICT, "Retrieval published profile differs")
            self.pid = data["pid"]
        self.connected = True
        try:
            yield self
        finally:
            self.connected = False

    async def retrieve(
        self, text: str, scope: DenseScope, budget: ExecutionBudget, *, top_k: int = 3
    ) -> DenseResult:
        rows = await self._call(NAMES[0], text, scope, budget, top_k)
        return next(iter(rows.values()))

    async def compare(
        self, text: str, scope: DenseScope, budget: ExecutionBudget, *, top_k: int = 3
    ) -> dict[str, DenseResult]:
        return await self._call(NAMES[1], text, scope, budget, top_k)

    @observed("retrieval.remote")
    async def _call(
        self, name: str, text: str, scope: DenseScope, budget: ExecutionBudget, top_k: int
    ) -> dict[str, DenseResult]:
        if not self.connected:
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "Retrieval MCP is not connected")
        if scope != self.binding.scope:
            raise AppError(ErrorCode.FORBIDDEN, "Retrieval scope is not owned")
        if self.binding.rerank_policy and top_k != self.binding.top_k:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Final top_k differs from the release")
        context = request_execution.get()
        if context is None:
            raise AppError(ErrorCode.FORBIDDEN, "Trusted request context required for retrieval")
        try:
            query = RetrievalQuery(query=text, top_k=top_k, scope_id=scope.fingerprint)
        except ValidationError:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Invalid retrieval query") from None
        cap = self.binding.allowance(text, budget.remaining_seconds())
        delegation = RetrievalDelegation(
            version=self.binding.version,
            context=RetrievalContext.from_request(context),
            name=cast(Literal["retrieve_intents", "compare_intents"], name),
            query_hash=hashlib.sha256(canonical(query.model_dump(mode="json"))).hexdigest(),
            profile_id=self.binding.profile_id,
            allowance=cap,
        )
        call_id = "retrieval-" + uuid4().hex
        meta = self.credentials.protect(
            "retrieval",
            {
                "action": "retrieve",
                "nonce": call_id,
                "delegation": delegation.model_dump(mode="json"),
            },
        )
        record: dict[str, Any] = {
            "call_id": call_id,
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "tool": name,
            "status": "not_dispatched",
            "model_calls": 0,
        }
        try:
            # One atomic application reservation persists before any remote action.
            budget.claim_attempt(
                retry=False, attempts=cap.attempts, tokens=cap.tokens, cost=cap.cost
            )
            record.update(status="unknown", model_calls=None)
            async with asyncio.timeout(cap.seconds):
                try:
                    result = await self.network.call_tool(
                        name, query.model_dump(mode="json"), meta=meta
                    )
                except RuntimeError:
                    raise AppError(
                        ErrorCode.MODEL_OUTPUT_INVALID, "Retrieval SDK rejected result schema"
                    ) from None
                except httpx2.TimeoutException:
                    raise AppError(ErrorCode.TIMEOUT, "Retrieval transport timed out") from None
                except Exception:
                    raise AppError(
                        ErrorCode.UPSTREAM_UNAVAILABLE, "Retrieval transport unavailable"
                    ) from None
            data = result.structured_content
            if isinstance(data, dict) and set(data) == {"error"}:
                error = ErrorDetail.model_validate(data["error"])
                raise AppError(
                    error.code, "Retrieval delegation rejected", retryable=error.retryable
                )
            if (
                len(result.content) != 1
                or not isinstance(result.content[0], TextContent)
                or json.loads(result.content[0].text) != data
                or len(canonical(data)) > 1048576
            ):
                raise ValueError
            wire = RetrievalWire.model_validate(data)
            proof = self.receipts.verify(cast(RequestParamsMeta, wire.proof))
            reply = wire.reply
            if (
                set(proof) != {"action", "nonce", "reply_hash"}
                or proof["action"] != "receipt"
                or proof["nonce"] != call_id
                or reply.call_id != call_id
                or proof["reply_hash"]
                != hashlib.sha256(canonical(reply.model_dump(mode="json"))).hexdigest()
                or reply.delegation != delegation
                or reply.version != self.binding.version
                or reply.query != query
                or result.is_error != (reply.error is not None)
                or sum(a.attempts for a in reply.actions) != reply.used.attempts
                or any(a.model_calls > a.attempts for a in reply.actions)
                or sum(a.model_calls for a in reply.actions)
                > (2 if self.binding.rerank_policy and self.binding.rerank_policy.enabled else 1)
                or any(
                    a.model_calls and a.action not in {"embedding", "rerank"} for a in reply.actions
                )
                or sum(a.model_calls for a in reply.actions if a.action == "embedding") > 1
                or sum(a.model_calls for a in reply.actions if a.action == "rerank")
                > (1 if self.binding.rerank_policy and self.binding.rerank_policy.enabled else 0)
                or sum(a.tokens for a in reply.actions) != reply.used.tokens
                or sum(a.charged_tokens for a in reply.actions) != reply.used.token_upper_bound
            ):
                raise ValueError
            if reply.error:
                if reply.results:
                    raise ValueError
            else:
                validate_results(
                    self.binding, query, name, {str(k): v for k, v in reply.results.items()}
                )
                if ranked := reply.results.get("hybrid_rerank"):
                    state = ranked.rerank
                    assert state is not None
                    native = [a for a in reply.actions if a.action == "rerank"]
                    if (
                        sum(a.attempts for a in native) != state.attempts
                        or (state.status == "passed" and sum(a.model_calls for a in native) != 1)
                        or (
                            state.result
                            and state.result.usage
                            and sum(a.tokens for a in native) != state.result.usage.total_tokens
                        )
                    ):
                        raise ValueError
            budget.settle_remote(cap.attempts, cap.tokens, cap.cost, reply.used)
            record.update(
                status="failed" if reply.error else "succeeded",
                model_calls=sum(a.model_calls for a in reply.actions),
                receipt=reply.model_dump(mode="json"),
                client_validation="PASS",
                server_validation="PASS",
            )
            self.pid = reply.service_pid
            if reply.error:
                raise AppError(
                    reply.error.code,
                    "Retrieval execution rejected",
                    retryable=reply.error.retryable,
                )
            return {str(k): v for k, v in reply.results.items()}
        except TimeoutError:
            raise AppError(ErrorCode.TIMEOUT, "Retrieval request timed out") from None
        except ValidationError, ValueError, TypeError, KeyError:
            raise AppError(
                ErrorCode.MODEL_OUTPUT_INVALID, "Invalid retrieval receipt or candidates"
            ) from None
        finally:
            self.diagnostics.append(record)
            del self.diagnostics[:-64]
            if self.on_record:
                self.on_record(record)

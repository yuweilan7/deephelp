import asyncio
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from deephelp_app.domain.models import ChatMessage, ChatRequest, ErrorCode, ModelTool
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.execution import AsyncCalls, ExecutionBudget
from deephelp_app.gateway import QianwenGateway
from deephelp_app.learning.model_fakes import FakeGateway
from deephelp_app.providers import EndpointConfig, ProviderConfig, create_gateway

pytestmark = pytest.mark.unit


async def test_larger_context_output_and_tool_catalog_reach_provider():
    messages = [ChatMessage(role="user", content="synthetic " * 200) for _ in range(40)]
    tools = [ModelTool(name=f"tool_{i}", parameters={"type": "object"}) for i in range(17)]

    def handler(request):
        payload = json.loads(request.content)
        assert len(request.content) > 65536
        assert len(payload["messages"]) == 40 and len(payload["tools"]) == 17
        assert payload["max_tokens"] == 8192
        return httpx.Response(200, json=chat_data())

    g = gateway(handler)
    try:
        result = await g.chat(
            ChatRequest(messages=messages, tools=tools, max_output_tokens=8192),
            budget(tokens=200000, cost="3"),
        )
        assert result.content == "OK"
    finally:
        await g.client.aclose()


async def test_thinking_is_configurable_and_hidden_text_stays_out_of_diagnostics():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["enable_thinking"] and payload["thinking_budget"] == 512
        data = chat_data()
        data["choices"][0]["message"]["reasoning_content"] = "PRIVATE_HIDDEN_REASONING"
        return httpx.Response(200, json=data)

    g = gateway(handler, enable_thinking=True)
    try:
        result = await g.chat(chat_request(thinking_budget=512), budget())
        assert result.content == "OK" and "PRIVATE_HIDDEN_REASONING" not in str(g.diagnostics)
        assert g.diagnostics[-1]["reasoning_content_length"] == len("PRIVATE_HIDDEN_REASONING")
    finally:
        await g.client.aclose()


async def test_request_can_disable_provider_thinking_default():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["enable_thinking"] is False and "thinking_budget" not in payload
        return httpx.Response(200, json=chat_data())

    g = gateway(handler, enable_thinking=True)
    try:
        assert (await g.chat(chat_request(enable_thinking=False), budget())).content == "OK"
    finally:
        await g.client.aclose()


async def test_thinking_allowance_is_reserved_before_dispatch():
    dispatched = False

    def handler(request):
        nonlocal dispatched
        dispatched = True
        return httpx.Response(200, json=chat_data())

    g = gateway(handler)
    try:
        with pytest.raises(AppError) as exc:
            await g.chat(
                chat_request(enable_thinking=True, thinking_budget=8192), budget(tokens=3000)
            )
        assert exc.value.code == ErrorCode.BUDGET_EXHAUSTED and not dispatched
    finally:
        await g.client.aclose()


def budget(attempts=5, retries=0, tokens=50000, cost="1"):
    return ExecutionBudget.start(5, attempts, retries, token_limit=tokens, cost_limit=Decimal(cost))


def chat_request(**kwargs):
    return ChatRequest(
        messages=[ChatMessage(role="user", content="PRIVATE_SYNTHETIC_PROMPT")], **kwargs
    )


def chat_data(content="OK", reason="stop", calls=None):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return {
        "model": "synthetic",
        "id": "safe-request",
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
        },
        "choices": [{"message": message, "finish_reason": reason}],
    }


def embed_data(rows=None, model="synthetic"):
    return {
        "model": model,
        "id": "safe-embedding",
        "usage": {
            "prompt_tokens": 4,
            "total_tokens": 4,
        },
        "data": rows
        if rows is not None
        else [
            {"index": 1, "embedding": [0, 2]},
            {"index": 0, "embedding": [2, 0]},
        ],
    }


def gateway(handler, *, fallback=False, calls=None, **config_values):
    endpoint = EndpointConfig(
        model="synthetic", input_cny_per_million=Decimal("8"), output_cny_per_million=Decimal("8")
    )
    embedding = (
        endpoint.model_copy(update={"model": "qwen3.7-text-embedding-flash"})
        if fallback
        else endpoint
    )
    config = ProviderConfig(
        chat=endpoint,
        embedding=embedding,
        dimension=2,
        allow_zero_embedding_indices=fallback,
        **config_values,
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    result = QianwenGateway(
        client,
        calls or AsyncCalls(1, 1, backoff=0),
        config,
        ("https://synthetic.invalid", "SECRET_KEY"),
        ("https://synthetic.invalid", "SECRET_EMBED_KEY"),
    )
    return result


async def test_reorders_valid_indices_deduplicates_and_cache_does_not_charge():
    transport_calls = 0

    def handler(request):
        nonlocal transport_calls
        transport_calls += 1
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content)["input"] == ["a", "b"]
        return httpx.Response(200, json=embed_data())

    g = gateway(handler)
    try:
        b = budget(attempts=1)
        result = await g.embed([" a ", "b", "a"], b)
        assert result.vectors == [[1, 0], [0, 1], [1, 0]]
        assert result.cache_hits == 1 and not result.position_index_fallback
        before = b.usage()
        result.vectors[0][0] = 99
        cached = await g.embed(["b", "a"], b)
        assert cached.vectors == [[0, 1], [1, 0]]
        assert cached.cache_hits == 2 and cached.usage.total_tokens == 0
        assert b.usage() == before and transport_calls == 1
    finally:
        await g.client.aclose()


async def test_observed_all_zero_index_response_uses_explicit_model_adapter():
    rows = [{"index": 0, "embedding": [2, 0]}, {"index": 0, "embedding": [0, 2]}]
    g = gateway(
        lambda r: httpx.Response(200, json=embed_data(rows, "qwen3.7-text-embedding-flash")),
        fallback=True,
    )
    try:
        result = await g.embed(["a", "b", "a"], budget())
        assert result.vectors == [[1, 0], [0, 1], [1, 0]]
        assert result.position_index_fallback
        assert any(d.get("position_index_fallback") for d in g.diagnostics)
    finally:
        await g.client.aclose()


@pytest.mark.parametrize("indices", [[0, 0], [1, 1], [-1, 0], [0, 2], [False, 1]])
async def test_bad_indices_are_rejected_without_generic_position_guess(indices):
    rows = [{"index": index, "embedding": [1, 0]} for index in indices]
    g = gateway(lambda r: httpx.Response(200, json=embed_data(rows)))
    try:
        b = budget()
        with pytest.raises(AppError) as error:
            await g.embed(["a", "b"], b)
        assert error.value.code == ErrorCode.MODEL_OUTPUT_INVALID
        assert error.value.provider_request_id == "safe-embedding"
        assert b.attempts_used == 1
        assert b.tokens_used == 4  # Bad response is still billed usage.
        expected_indices = [i if type(i) is int else None for i in indices]
        assert any(d.get("indices") == expected_indices for d in g.diagnostics)
    finally:
        await g.client.aclose()


@pytest.mark.parametrize(
    "vector", [[1], [0, 0], ["error", 1], [True, 0], [float("nan"), 0], [float("inf"), 0]]
)
async def test_bad_vector_not_accepted_as_normal_result(vector):
    # JSON-encoded NaN is deliberately injected; it must never enter cache/result.
    data = embed_data([{"index": 0, "embedding": vector}])
    g = gateway(lambda r: httpx.Response(200, content=json.dumps(data)))
    try:
        with pytest.raises(AppError) as error:
            await g.embed(["a"], budget())
        assert error.value.code == ErrorCode.MODEL_OUTPUT_INVALID
        assert not g._cache
    finally:
        await g.client.aclose()


@pytest.mark.parametrize("texts", [[], [""], ["  "], [123]])
async def test_empty_or_invalid_inputs_start_no_attempt(texts):
    g = gateway(lambda r: pytest.fail("Invalid input must not reach HTTP"))
    try:
        b = budget()
        with pytest.raises(AppError):
            await g.embed(texts, b)
        assert b.attempts_used == 0
    finally:
        await g.client.aclose()


async def test_nfc_text_normalization_and_bounded_cache_eviction():
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(200, json=embed_data([{"index": 0, "embedding": [1, 0]}]))

    g = gateway(handler, cache_entries=1)
    try:
        b = budget()
        await g.embed(["e\u0301"], b)
        await g.embed(["é"], b)
        assert count == 1
        await g.embed(["other"], b)
        await g.embed(["é"], b)
        assert count == 3 and len(g._cache) == 1
        assert g._cache_bytes <= g.config.cache_bytes
        assert (
            g.signature.fingerprint != g.signature.model_copy(update={"dimension": 3}).fingerprint
        )
        assert (
            g.signature.fingerprint != g.signature.model_copy(update={"revision": "v2"}).fingerprint
        )
        assert g.signature.revision is None
    finally:
        await g.client.aclose()


async def test_embedding_batches_preserve_global_order():
    sizes = []

    def handler(request):
        inputs = json.loads(request.content)["input"]
        sizes.append(len(inputs))
        rows = [{"index": index, "embedding": [1, int(text)]} for index, text in enumerate(inputs)]
        return httpx.Response(200, json=embed_data(list(reversed(rows))))

    g = gateway(handler, batch_size=2, normalization="none")
    try:
        result = await g.embed(["1", "2", "3", "4", "5", "1"], budget())
        assert result.vectors == [[1, i] for i in [1, 2, 3, 4, 5, 1]]
        assert sizes == [2, 2, 1] and result.usage.input_tokens == 12
    finally:
        await g.client.aclose()


async def test_429_retry_is_bounded_and_unknown_charge_not_refunded():
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(429, text="PRIVATE_PROVIDER_ERROR")
        return httpx.Response(200, json=chat_data())

    g = gateway(handler)
    try:
        b = budget(retries=1)
        result = await g.chat(chat_request(), b)
        assert result.content == "OK" and count == 2
        assert b.attempts_used == 2 and b.retries_used == 1
        assert b.tokens_used == 12 and b.token_upper_bound > 12 and b.uncertain_attempts == 1
        assert b.cost_upper_bound > b.cost_used
        recorded = json.dumps(g.diagnostics)
        assert all(
            secret not in recorded
            for secret in ["SECRET_KEY", "PRIVATE_PROVIDER_ERROR", "PRIVATE_SYNTHETIC_PROMPT"]
        )
    finally:
        await g.client.aclose()


@pytest.mark.parametrize(
    "status,code,expected",
    [
        (401, "invalid_api_key", ErrorCode.UNAUTHENTICATED),
        (403, "Forbidden", ErrorCode.FORBIDDEN),
        (403, "AllocationQuota.FreeTierOnly", ErrorCode.PROVIDER_QUOTA_EXHAUSTED),
        (429, "insufficient_quota", ErrorCode.PROVIDER_QUOTA_EXHAUSTED),
        (400, "unsupported_schema", ErrorCode.MODEL_CAPABILITY_UNAVAILABLE),
        (500, "server_error", ErrorCode.UPSTREAM_UNAVAILABLE),
    ],
)
async def test_provider_error_classification(status, code, expected):
    g = gateway(
        lambda r: httpx.Response(
            status,
            json={"error": {"code": code, "message": "SECRET"}},
            headers={"x-request-id": "safe-error"},
        )
    )
    try:
        b = budget(retries=0)
        with pytest.raises(AppError) as error:
            await g.chat(chat_request(), b)
        assert error.value.code == expected and error.value.provider_request_id == "safe-error"
        assert "SECRET" not in str(error.value) and b.attempts_used == 1
    finally:
        await g.client.aclose()


async def test_timeout_and_cancellation_keep_unknown_usage_and_release_slot():
    entered = asyncio.Event()
    cleaned = 0
    count = 0

    async def handler(request):
        nonlocal cleaned, count
        count += 1
        if count == 1:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned += 1
        return httpx.Response(200, json=chat_data())

    g = gateway(handler, calls=AsyncCalls(1, 1, backoff=0))
    try:
        b = budget()
        task = asyncio.create_task(g.chat(chat_request(), b))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned == 1 and b.uncertain_attempts == 1
        assert (await g.chat(chat_request(), b)).content == "OK"
        assert count == 2
    finally:
        await g.client.aclose()


@pytest.mark.parametrize("limit,cost", [(1, "1"), (50000, "0.000001")])
async def test_token_and_cost_admission_happens_before_http_attempt(limit, cost):
    g = gateway(lambda r: pytest.fail("Over-budget request reached HTTP"))
    try:
        b = budget(tokens=limit, cost=cost)
        with pytest.raises(AppError) as error:
            await g.chat(chat_request(), b)
        assert error.value.code == ErrorCode.BUDGET_EXHAUSTED and b.attempts_used == 0
    finally:
        await g.client.aclose()


async def test_concurrent_requests_share_single_attempt_atomically():
    g = gateway(lambda r: httpx.Response(200, json=chat_data()), calls=AsyncCalls(2, 1, backoff=0))
    try:
        b = budget(attempts=1)
        results = await asyncio.gather(
            g.chat(chat_request(), b), g.chat(chat_request(), b), return_exceptions=True
        )
        assert sum(isinstance(item, AppError) for item in results) == 1
        assert b.attempts_used == 1
    finally:
        await g.client.aclose()


SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string"}},
    "required": ["order_id"],
    "additionalProperties": False,
}


@pytest.mark.parametrize(
    "content,reason",
    [
        ("{", "stop"),
        ('{"order_id":7}', "stop"),
        ('{"order_id":"0007","extra":true}', "stop"),
        ('{"order_id":"0007"}', "length"),
    ],
)
async def test_invalid_or_truncated_json_is_billed_but_not_silently_accepted(content, reason):
    g = gateway(lambda r: httpx.Response(200, json=chat_data(content, reason)))
    try:
        b = budget(retries=2)
        with pytest.raises(AppError) as error:
            await g.chat(chat_request(response_format="json_schema", output_schema=SCHEMA), b)
        assert error.value.code == ErrorCode.MODEL_OUTPUT_INVALID
        assert b.attempts_used == 1 and b.tokens_used == 12
    finally:
        await g.client.aclose()


async def test_one_schema_repair_uses_same_budget_and_sums_usage():
    outputs = iter([chat_data("{"), chat_data('{"order_id":"0007"}')])
    g = gateway(lambda r: httpx.Response(200, json=next(outputs)))
    try:
        b = budget()
        result = await g.chat(
            chat_request(response_format="json_object", output_schema=SCHEMA, repair_once=True), b
        )
        assert result.structured == {"order_id": "0007"}
        assert result.usage.total_tokens == b.tokens_used == 24
        assert b.attempts_used == 2 and b.retries_used == 0
    finally:
        await g.client.aclose()


@pytest.mark.parametrize(
    "name,args,reason",
    [
        ("unknown", '{"order_id":"0007"}', "tool_calls"),
        ("lookup", '{"order_id":7}', "tool_calls"),
        ("lookup", '{"order_id":"0007"}', "length"),
    ],
)
async def test_unknown_or_invalid_tool_call_never_executed(name, args, reason):
    calls = [{"id": "call-1", "type": "function", "function": {"name": name, "arguments": args}}]
    g = gateway(lambda r: httpx.Response(200, json=chat_data(None, reason, calls)))
    try:
        with pytest.raises(AppError) as error:
            await g.chat(
                chat_request(
                    tools=[ModelTool(name="lookup", parameters=SCHEMA)], tool_choice="lookup"
                ),
                budget(),
            )
        assert error.value.code == ErrorCode.MODEL_OUTPUT_INVALID
    finally:
        await g.client.aclose()


async def test_actual_structured_tool_fields_are_accepted_even_with_stop_reason():
    calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "lookup", "arguments": '{"order_id":"0007"}'},
        }
    ]
    g = gateway(lambda r: httpx.Response(200, json=chat_data(None, "stop", calls)))
    try:
        result = await g.chat(
            chat_request(tools=[ModelTool(name="lookup", parameters=SCHEMA)], tool_choice="lookup"),
            budget(),
        )
        assert result.tool_calls[0].arguments == {"order_id": "0007"}
        assert result.finish_reason == "stop"
    finally:
        await g.client.aclose()


async def test_external_schema_reference_disabled_before_call():
    g = gateway(lambda r: pytest.fail("External schema must be refused"))
    try:
        b = budget()
        with pytest.raises(AppError) as error:
            await g.chat(
                chat_request(
                    response_format="json_schema",
                    output_schema={"$ref": "https://private.invalid/schema"},
                ),
                b,
            )
        assert error.value.code == ErrorCode.INVALID_ARGUMENT and b.attempts_used == 0
    finally:
        await g.client.aclose()


async def test_fake_fixture_replays_chat_and_embedding_with_zero_cost():
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/m03-synthetic.json").read_text())
    g = FakeGateway(fixture["responses"])
    try:
        b = budget()
        assert await g.complete("synthetic", b) == "SYNTHETIC_OK"
        assert (await g.embed(["a", "b"], b)).vectors == [[1, 0], [0, 1]]
        assert g.call_count == 2 and b.cost_used == 0
    finally:
        await g.aclose()


async def test_factory_supports_separate_endpoint_permissions_and_key_fields():
    observed = []

    def handler(request):
        observed.append((request.url.host, request.headers["authorization"]))
        return httpx.Response(
            200, json=chat_data() if request.url.path.endswith("completions") else embed_data()
        )

    endpoint = EndpointConfig(model="synthetic", input_cny_per_million=Decimal("1"))
    config = ProviderConfig(
        chat=endpoint,
        embedding=endpoint.model_copy(
            update={"base_url_env": "EMBED_URL", "api_key_env": "EMBED_KEY"}
        ),
        dimension=2,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        g = create_gateway(
            client,
            config,
            AsyncCalls(1, 1),
            env={
                "DEEPHELP_MODEL_BASE_URL": "https://chat.invalid/v1",
                "DEEPHELP_MODEL_API_KEY": "chat-secret",
                "EMBED_URL": "https://embed.invalid/v1",
                "EMBED_KEY": "embed-secret",
            },
        )
        b = budget()
        await g.chat(chat_request(), b)
        await g.embed(["a", "b"], b)
    assert observed == [
        ("chat.invalid", "Bearer chat-secret"),
        ("embed.invalid", "Bearer embed-secret"),
    ]


def test_provider_config_rejects_unverified_fallback_and_invalid_credentials(tmp_path):
    payload = json.loads(Path("modules/deephelp-app/providers.example.json").read_text())
    payload["embedding"]["model"] = "unverified"
    path = tmp_path / "provider.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ConfigurationError):
        ProviderConfig.load(path)


async def test_model_timeout_has_exact_attempts_and_retains_both_reservations():
    cleaned = 0

    async def handler(request):
        nonlocal cleaned
        try:
            await asyncio.Event().wait()
        finally:
            cleaned += 1

    g = gateway(handler, calls=AsyncCalls(1, 0.01, backoff=0))
    try:
        b = budget(retries=1)
        with pytest.raises(AppError) as error:
            await g.chat(chat_request(), b)
        assert error.value.code == ErrorCode.TIMEOUT
        assert cleaned == b.attempts_used == b.uncertain_attempts == 2
        assert b.retries_used == 1 and b.tokens_used == 0
        assert b.token_upper_bound > 0 and b.cost_upper_bound > 0
    finally:
        await g.client.aclose()


async def test_structural_cassette_never_records_prompt_content_or_credentials(tmp_path):
    g = gateway(lambda r: httpx.Response(200, json=chat_data("PRIVATE_SYNTHETIC_RESPONSE")))
    try:
        await g.chat(chat_request(), budget())
        output = tmp_path / "cassette.json"
        g.record_diagnostics(output)
        serialized = output.read_text()
        assert json.loads(serialized)["format"] == "m03-structure-v1"
        assert all(
            value not in serialized
            for value in [
                "PRIVATE_SYNTHETIC_RESPONSE",
                "PRIVATE_SYNTHETIC_PROMPT",
                "SECRET_KEY",
                "synthetic.invalid",
            ]
        )
    finally:
        await g.client.aclose()

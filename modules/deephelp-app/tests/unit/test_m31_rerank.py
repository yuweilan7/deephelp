import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from deephelp_app.adapters.providers import (
    EndpointConfig,
    ProviderConfig,
    RerankEndpointConfig,
    create_gateway,
)
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.execution import AsyncCalls, ExecutionBudget
from deephelp_app.domain.models.common import ErrorCode
from deephelp_app.domain.models.intents import IntentCode
from deephelp_app.domain.models.model_io import RerankDocument, RerankRequest

pytestmark = pytest.mark.unit
URL = "https://rerank.invalid/api/v1/services/rerank/text-rerank/text-rerank"
DOCUMENTS = (
    RerankDocument(
        doc_id="coupon",
        content="券不能使用",
        intent_code=IntentCode.COUPON_UNUSABLE,
        source="reference-coupon",
    ),
    RerankDocument(
        doc_id="discount",
        content="支付没有减价",
        intent_code=IntentCode.DISCOUNT_MISSING,
        source="reference-discount",
    ),
    RerankDocument(
        doc_id="activity",
        content="订单参加的活动",
        intent_code=IntentCode.ORDER_ACTIVITY_QUERY,
        source="reference-activity",
    ),
)


def request(**kwargs):
    return RerankRequest(
        query="券用不了", documents=DOCUMENTS, top_n=kwargs.pop("top_n", 3), **kwargs
    )


def response(rows=None, **extra):
    return {
        "output": {
            "results": rows
            if rows is not None
            else [
                {"index": 0, "relevance_score": 0.9},
                {"index": 2, "relevance_score": 0.4},
                {"index": 1, "relevance_score": 0.1},
            ]
        },
        "request_id": "native-safe-id",
        "usage": {"prompt_tokens": 20, "total_tokens": 20},
        **extra,
    }


def budget(*, tokens=30000, retries=0, attempts=4):
    return ExecutionBudget.start(5, attempts, retries, token_limit=tokens, cost_limit=Decimal("1"))


def config(*, enabled=True, **kwargs):
    other = EndpointConfig(model="synthetic", input_cny_per_million=Decimal("8"))
    return ProviderConfig(
        chat=other,
        embedding=other,
        rerank=RerankEndpointConfig(input_cny_per_million=Decimal("8")) if enabled else None,
        **kwargs,
    )


def gateway(handler, *, calls=None, **kwargs):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    return create_gateway(
        client,
        config(**kwargs),
        calls or AsyncCalls(1, 1, backoff=0),
        env={
            "DEEPHELP_MODEL_BASE_URL": "https://chat.invalid/compatible-mode/v1",
            "DEEPHELP_MODEL_API_KEY": "PRIVATE_KEY",
            "DEEPHELP_RERANK_URL": URL,
        },
    )


async def test_native_request_maps_original_evidence_and_stable_ties():
    def handler(req):
        assert str(req.url) == URL and req.method == "POST"
        assert req.headers["authorization"] == "Bearer PRIVATE_KEY"
        payload = json.loads(req.content)
        assert payload == {
            "model": "qwen3.7-text-rerank",
            "input": {"query": "券用不了", "documents": [d.content for d in DOCUMENTS]},
            "parameters": {"top_n": 3, "instruct": "similar"},
        }
        assert (
            "intent_code" not in req.content.decode()
            and "reference-coupon" not in req.content.decode()
        )
        return httpx.Response(
            200,
            json=response(
                [
                    {
                        "index": 2,
                        "relevance_score": 2.5,
                        "document": {"text": "FORGED_CONTENT"},
                        "doc_id": "FORGED_ID",
                        "intent_code": "DISCOUNT_MISSING",
                    },
                    {"index": 0, "relevance_score": 2.5},
                    {"index": 1, "relevance_score": -3.0},
                ]
            ),
        )

    g = gateway(handler)
    try:
        b = budget()
        result = await g.rerank(request(instruct="similar"), b)
        assert [h.index for h in result.hits] == [0, 2, 1]
        assert result.hits[1].document is DOCUMENTS[2]
        assert all(h.score_kind == "rerank" for h in result.hits)
        assert result.usage.total_tokens == b.tokens_used == b.token_upper_bound == 20
        assert b.attempts_used == 1 and b.uncertain_attempts == 0
        assert b.cost_used == b.cost_upper_bound == Decimal("0.00016")
        assert result.provider_request_id == "native-safe-id"
        diagnostic = json.dumps(g.diagnostics)
        assert all(s not in diagnostic for s in ("PRIVATE_KEY", "FORGED_CONTENT", "券用不了", URL))
        with pytest.raises(ValidationError):
            result.hits[0].document.content = "changed"
    finally:
        await g.client.aclose()


async def test_partial_top_n_and_default_instruction_use_native_protocol():
    def handler(req):
        assert json.loads(req.content)["parameters"] == {"top_n": 1}
        return httpx.Response(200, json=response([{"index": 2, "relevance_score": 0.5}]))

    g = gateway(handler)
    try:
        result = await g.rerank(request(top_n=1), budget())
        assert result.hits[0].document == DOCUMENTS[2] and result.instruct is None
    finally:
        await g.client.aclose()


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"index": 0, "relevance_score": 1}],
        [{"index": i, "relevance_score": 1} for i in [0, 0, 2]],
        [{"index": i, "relevance_score": 1} for i in [True, 1, 2]],
        [{"index": i, "relevance_score": 1} for i in ["0", 1, 2]],
        [{"index": i, "relevance_score": 1} for i in [-1, 1, 2]],
        [{"index": i, "relevance_score": 1} for i in [3, 1, 2]],
        [{"index": i, "relevance_score": 1} for i in [0.0, 1, 2]],
        [{"index": i, "relevance_score": s} for i, s in enumerate([0.1, 0.9, 0.2])],
        [{"index": i, "relevance_score": s} for i, s in enumerate([float("nan"), 0.5, 0])],
        [{"index": i, "relevance_score": s} for i, s in enumerate([float("inf"), 0.5, 0])],
        [{"index": i, "relevance_score": s} for i, s in enumerate([True, 0.5, 0])],
        [{"index": i, "relevance_score": s} for i, s in enumerate(["1", 0.5, 0])],
        [{"index": i, "relevance_score": s} for i, s in enumerate([None, 0.5, 0])],
        [None, {"index": 1}, {"index": 2}],
        [{"index": 0}, {"index": 1}, {"index": 2}],
    ],
)
async def test_invalid_mapping_or_score_is_rejected_without_retry(rows):
    g = gateway(lambda req: httpx.Response(200, content=json.dumps(response(rows))))
    try:
        b = budget(retries=1)
        with pytest.raises(AppError) as exc:
            await g.rerank(request(), b)
        assert exc.value.code == ErrorCode.MODEL_OUTPUT_INVALID
        assert exc.value.provider_request_id == "native-safe-id"
        assert b.attempts_used == 1 and b.retries_used == 0
        assert b.tokens_used == 20  # Valid usage is charged even when content fails validation.
        assert g.diagnostics[-1]["validation"] == "failed"
    finally:
        await g.client.aclose()


@pytest.mark.parametrize(
    "data",
    [
        response(output={}),
        response(output=[]),
        response(model="qwen3-rerank"),
        response(usage={"prompt_tokens": True, "total_tokens": 1}),
        response(usage={"prompt_tokens": 20, "total_tokens": 21}),
        response(usage={"prompt_tokens": 20, "total_tokens": 21, "completion_tokens": 1}),
        response(usage={"total_tokens": "20"}),
    ],
)
async def test_native_envelope_and_usage_must_be_valid(data):
    g = gateway(lambda req: httpx.Response(200, json=data))
    try:
        with pytest.raises(AppError) as exc:
            await g.rerank(request(), budget())
        assert exc.value.code == ErrorCode.MODEL_OUTPUT_INVALID
    finally:
        await g.client.aclose()


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": 20}, {"total_tokens": 20}])
async def test_missing_usage_stays_unknown_and_retains_full_reservation(usage):
    data = response()
    if usage is None:
        data.pop("usage")
    else:
        data["usage"] = usage
    g = gateway(lambda req: httpx.Response(200, json=data))
    try:
        b = budget()
        result = await g.rerank(request(), b)
        assert result.usage is None and b.tokens_used == 0 and b.uncertain_attempts == 1
        assert b.token_upper_bound > 0 and b.cost_upper_bound > 0
        assert g.diagnostics[0]["api_usage"]["total_tokens"] == (usage or {}).get("total_tokens")
    finally:
        await g.client.aclose()


async def test_repeated_query_is_reserved_for_every_document_before_dispatch():
    sent = False

    def handler(req):
        nonlocal sent
        sent = True
        return httpx.Response(200, json=response())

    g = gateway(handler)
    try:
        req = RerankRequest(query="问" * 500, documents=DOCUMENTS, top_n=3)
        # Serialized body would fit; pairwise query repetition takes it over this budget.
        with pytest.raises(AppError) as exc:
            await g.rerank(req, budget(tokens=4000))
        assert exc.value.code == ErrorCode.BUDGET_EXHAUSTED and not sent
    finally:
        await g.client.aclose()


async def test_native_retry_retains_unknown_attempt_and_charges_success_once():
    sent = 0

    def handler(req):
        nonlocal sent
        sent += 1
        if sent == 1:
            return httpx.Response(
                503, json={"code": "ServiceUnavailable", "request_id": "native-failed-id"}
            )
        return httpx.Response(200, json=response())

    g = gateway(handler)
    try:
        b = budget(retries=1)
        result = await g.rerank(request(), b)
        assert result.usage.total_tokens == b.tokens_used == 20
        assert sent == b.attempts_used == 2 and b.retries_used == b.uncertain_attempts == 1
        assert b.token_upper_bound > 20 and b.cost_upper_bound > b.cost_used
    finally:
        await g.client.aclose()


@pytest.mark.parametrize(
    "status,code,expected",
    [
        (401, "InvalidApiKey", ErrorCode.UNAUTHENTICATED),
        (403, "AccessDenied", ErrorCode.FORBIDDEN),
        (400, "InvalidParameter", ErrorCode.MODEL_CAPABILITY_UNAVAILABLE),
        (429, "Throttling", ErrorCode.RATE_LIMITED),
        (400, "QuotaExceeded", ErrorCode.PROVIDER_QUOTA_EXHAUSTED),
        (200, "QuotaExceeded", ErrorCode.PROVIDER_QUOTA_EXHAUSTED),
    ],
)
async def test_native_provider_errors_are_structural_and_not_success(status, code, expected):
    g = gateway(
        lambda req: httpx.Response(
            status, json={"code": code, "request_id": "safe-error-id", "message": "PRIVATE_DETAIL"}
        )
    )
    try:
        b = budget()
        with pytest.raises(AppError) as exc:
            await g.rerank(request(), b)
        assert exc.value.code == expected and exc.value.provider_code == code
        assert exc.value.provider_request_id == "safe-error-id"
        assert b.attempts_used == b.uncertain_attempts == 1
        assert "PRIVATE_DETAIL" not in str(g.diagnostics) + str(exc.value)
    finally:
        await g.client.aclose()


@pytest.mark.parametrize("cancel", [True, False])
async def test_cancel_and_timeout_release_stream_without_refunding_unknown_usage(cancel):
    entered, closed = asyncio.Event(), asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b"unreachable"

        async def aclose(self):
            closed.set()

    g = gateway(lambda req: httpx.Response(200, stream=Stream()), calls=AsyncCalls(1, 0.05))
    try:
        b = budget()
        task = asyncio.create_task(g.rerank(request(), b))
        await entered.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(AppError) as exc:
                await task
            assert exc.value.code == ErrorCode.TIMEOUT
        assert closed.is_set() and b.attempts_used == b.uncertain_attempts == 1
        assert b.token_upper_bound > 0 and b.tokens_used == 0
    finally:
        await g.client.aclose()


async def test_disabled_capability_does_not_dispatch_and_old_configuration_loads():
    g = gateway(lambda req: pytest.fail("disabled rerank dispatched"), enabled=False)
    try:
        b = budget()
        with pytest.raises(AppError) as exc:
            await g.rerank(request(), b)
        assert exc.value.code == ErrorCode.MODEL_CAPABILITY_UNAVAILABLE and b.attempts_used == 0
    finally:
        await g.client.aclose()


@pytest.mark.parametrize(
    "url",
    [
        "http://rerank.invalid",
        "https://rerank.invalid/reranks",
        "https://rerank.invalid/compatible-mode/v1",
        URL + "?secret=yes",
    ],
)
def test_native_endpoint_is_explicit_and_validated(url):
    with pytest.raises(ConfigurationError):
        create_gateway(
            None,
            config(),
            AsyncCalls(1, 1),
            env={
                "DEEPHELP_MODEL_BASE_URL": "https://chat.invalid",
                "DEEPHELP_MODEL_API_KEY": "key",
                "DEEPHELP_RERANK_URL": url,
            },
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"query": " "},
        {"documents": ()},
        {"top_n": True},
        {"top_n": 4},
        {"top_n": 0},
        {"instruct": " "},
        {"documents": (DOCUMENTS[0], DOCUMENTS[0])},
    ],
)
def test_invalid_local_request_never_reaches_gateway(changes):
    values = dict(query="query", documents=DOCUMENTS, top_n=3)
    values.update(changes)
    with pytest.raises(ValidationError):
        RerankRequest(**values)

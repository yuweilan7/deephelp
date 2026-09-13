import asyncio
import json
import socket
from uuid import uuid4

import httpx
import pytest
from fastapi import Request
from pydantic import SecretStr

from deephelp_app.app import create_app
from deephelp_app.errors import ConfigurationError
from deephelp_app.settings import Settings
from deephelp_app.trace import JsonlTrace, MemoryTrace, request_context

pytestmark = pytest.mark.integration


async def test_missing_key_fake_startup_and_shared_client_shutdown(message):
    trace = MemoryTrace()
    app = create_app(Settings(mode="test"), trace=trace)
    assert not hasattr(app.state, "resources")
    async with app.router.lifespan_context(app):
        pool = app.state.resources.client
        assert not pool.is_closed
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://local"
        ) as client:
            assert (await client.get("/health")).json()["status"] == "ok"
            response = await client.post("/converse", json=message)
            assert response.status_code == 501
            assert response.json()["error"]["code"] == "NOT_IMPLEMENTED"
            assert app.state.resources.client is pool
            assert app.state.resources.gateway.call_count == 0
            assert response.json()["run_id"] is None
            assert response.json()["question_id"] is None
            assert response.json()["budget_used"]["attempts"] == 0
        with pytest.raises(ConfigurationError, match="Network disabled"):
            await pool.get("https://synthetic.invalid")
    assert pool.is_closed
    assert trace.closed


async def test_startup_failure_closes_previously_opened_trace(monkeypatch):
    trace = MemoryTrace()

    def fail_client(*args, **kwargs):
        raise ValueError("synthetic client setup failure")

    app = create_app(Settings(mode="test"), trace=trace)
    monkeypatch.setattr("deephelp_app.app.httpx.AsyncClient", fail_client)
    with pytest.raises(ValueError):
        async with app.router.lifespan_context(app):
            pytest.fail("Startup should fail")
    assert trace.closed


async def test_live_missing_key_reports_configuration_error_before_resources():
    app = create_app(Settings(mode="live", enable_live=True), trace=MemoryTrace())
    with pytest.raises(ConfigurationError, match="DEEPHELP_MODEL_API_KEY"):
        async with app.router.lifespan_context(app):
            pytest.fail("Live must reject missing key")
    assert not hasattr(app.state, "resources")


async def test_configured_live_is_explicitly_not_implemented_without_model_call():
    settings = Settings(
        mode="live",
        enable_live=True,
        model_api_key=SecretStr("synthetic-key"),
        model_base_url="https://synthetic.invalid",
        live_model="synthetic",
        live_max_calls=1,
        live_max_tokens=10,
        live_max_cost="0.01",
    )
    app = create_app(settings, trace=MemoryTrace())
    with pytest.raises(ConfigurationError, match="NOT_IMPLEMENTED"):
        async with app.router.lifespan_context(app):
            pytest.fail("M01 does not implement live gateway")
    assert not hasattr(app.state, "resources")


async def test_interleaved_requests_keep_trace_context_and_ids_separate(message):
    class InterleavedTrace(MemoryTrace):
        def __init__(self):
            super().__init__()
            self.entered = 0
            self.both = asyncio.Event()

        async def emit(self, event):
            if event.event == "input_validated":
                self.entered += 1
                if self.entered == 2:
                    self.both.set()
                await self.both.wait()
                await asyncio.sleep(0)
            assert request_context.get() == (event.request_id, event.trace_id)
            await super().emit(event)

    trace = InterleavedTrace()
    app = create_app(Settings(mode="test"), trace=trace)
    ids = [(str(uuid4()), str(uuid4())), (str(uuid4()), str(uuid4()))]
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://local"
        ) as client:
            async with asyncio.timeout(2):
                results = await asyncio.gather(
                    *[
                        client.post(
                            "/converse",
                            json={**message, "message_id": f"msg-{i}"},
                            headers={"x-request-id": req_id, "x-trace-id": trace_id},
                        )
                        for i, (req_id, trace_id) in enumerate(ids)
                    ]
                )
    for result, (req_id, trace_id) in zip(results, ids, strict=True):
        assert result.headers["x-request-id"] == result.json()["request_id"] == req_id
        assert result.headers["x-trace-id"] == result.json()["trace_id"] == trace_id
        events = [event for event in trace.events if event.request_id == req_id]
        assert [event.event for event in events] == [
            "request_received",
            "input_validated",
            "request_finished",
        ]
        assert {event.trace_id for event in events} == {trace_id}
    assert request_context.get() is None


async def test_validation_and_internal_errors_never_expose_raw_input(message):
    trace = MemoryTrace()
    app = create_app(Settings(mode="test"), trace=trace)

    @app.get("/_synthetic_failure")
    async def failure():
        raise ValueError("synthetic-sensitive-detail")

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://local"
        ) as client:
            invalid = await client.post(
                "/converse", json={**message, "tenant_id": "synthetic-sensitive-detail"}
            )
            assert invalid.status_code == 422
            assert invalid.json()["error"]["code"] == "INVALID_ARGUMENT"
            failed = await client.get("/_synthetic_failure")
            assert failed.status_code == 500
            assert failed.json()["outcome"] == "ERROR"
            assert failed.json()["error"]["code"] == "INTERNAL_ERROR"
            assert failed.json()["request_id"] == failed.headers["x-request-id"]
            unknown = await client.get("/unknown")
            assert unknown.status_code == 404
            assert unknown.json()["error"]["code"] == "INVALID_ARGUMENT"
            assert "synthetic-sensitive-detail" not in invalid.text + failed.text
    assert "synthetic-sensitive-detail" not in "\n".join(
        event.model_dump_json() for event in trace.events
    )


async def test_untrusted_remote_address_cannot_use_local_identity(message):
    app = create_app(Settings(mode="test"), trace=MemoryTrace())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app, client=("192.0.2.1", 80))
        async with httpx.AsyncClient(transport=transport, base_url="http://local") as client:
            response = await client.post("/converse", json=message)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert response.json()["outcome"] == "REJECTED"


async def test_total_request_deadline_is_explicit_error_and_cleans_task():
    app = create_app(Settings(mode="test", request_timeout=0.03), trace=MemoryTrace())
    cleaned = asyncio.Event()

    @app.get("/_synthetic_slow")
    async def slow():
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://local"
        ) as client:
            response = await client.get("/_synthetic_slow")
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "BUDGET_EXHAUSTED"
    assert cleaned.is_set()


async def test_failed_request_reports_consumed_budget():
    app = create_app(Settings(mode="test", child_timeout=0.02), trace=MemoryTrace())

    @app.get("/_synthetic_budget_failure")
    async def failure(request: Request):
        async def blocked():
            await asyncio.Event().wait()

        return await request.app.state.resources.calls.call(
            blocked, request.state.budget, retry_safe=True
        )

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://local"
        ) as client:
            response = await client.get("/_synthetic_budget_failure")
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "TIMEOUT"
    assert response.json()["budget_used"]["attempts"] == 2
    assert response.json()["budget_used"]["retries"] == 1


async def test_invalid_diagnostic_headers_regenerate_safe_ids():
    app = create_app(Settings(mode="test"), trace=MemoryTrace())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://local"
        ) as client:
            response = await client.get(
                "/health", headers={"x-request-id": "unsafe-value", "x-trace-id": "bad-value"}
            )
    assert response.headers["x-request-id"] != "unsafe-value"
    assert response.headers["x-trace-id"] != "bad-value"


async def test_jsonl_trace_is_parseable_and_excludes_text_and_key(tmp_path, message):
    path = tmp_path / "trace.jsonl"
    trace = JsonlTrace(path)
    app = create_app(
        Settings(mode="test", model_api_key=SecretStr("synthetic-secret")), trace=trace
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://local"
        ) as client:
            await client.post("/converse", json=message)
    text = path.read_text(encoding="utf-8")
    records = [json.loads(line) for line in text.splitlines()]
    assert [record["event"] for record in records] == [
        "request_received",
        "input_validated",
        "request_finished",
    ]
    assert records[1]["input_length"] == len(message["raw_text"])
    assert message["raw_text"] not in text
    assert "synthetic-secret" not in text


def test_external_network_guard_rejects_connect_and_dns():
    with socket.socket() as sock:
        with pytest.raises(AssertionError, match="External network"):
            sock.connect(("203.0.113.1", 80))
    with pytest.raises(AssertionError, match="External DNS"):
        socket.getaddrinfo("synthetic.invalid", 443)

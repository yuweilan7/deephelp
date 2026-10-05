import json
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest

from deephelp_app.adapters.observability.langfuse_query import LangfuseRunClient


class Pool:
    def __init__(self, rows):
        self.rows = rows

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def cursor(self):
        yield self

    async def execute(self, sql, parameters):
        assert parameters == ("authorized-run",)
        assert "WHERE run_id=%s" in sql

    async def fetchall(self):
        return self.rows

    async def rollback(self):
        pass


def config(tmp_path):
    path = tmp_path / "native.json"
    path.write_text(
        json.dumps(
            {
                "host": "http://127.0.0.1:9",
                "public_key": "public",
                "secret_key": "SERVER_SECRET",
                "redaction_key": "ab" * 32,
            }
        )
    )
    return str(path)


def row(trace_id, index):
    return {
        "id": f"{index:016x}",
        "traceId": trace_id,
        "parentObservationId": None,
        "name": "stage.600",
        "type": "SPAN",
        "startTime": "2026-10-04T12:00:00Z",
        "endTime": "2026-10-04T12:00:01Z",
        "latency": 1,
        "metadata": {"service.name": "app", "prompt": "DO_NOT_EXPORT"},
        "input": {"order_id": "0001234", "password": "DO_NOT_EXPORT"},
        "output": {"reasoning_content": "DO_NOT_EXPORT", "answer": "订单0001234"},
    }


async def test_registered_run_pagination_and_defense_in_depth_redaction(tmp_path):
    identifier = uuid4().hex
    seen = []

    def handler(request):
        assert request.url.params["traceId"] == identifier
        assert request.headers["authorization"].startswith("Basic ")
        seen.append(request.url.params.get("cursor"))
        return httpx.Response(
            200,
            json={
                "data": [row(identifier, len(seen))],
                "meta": {"cursor": "second" if len(seen) == 1 else None},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        reader = LangfuseRunClient(http, config(tmp_path))
        result = await reader.read(
            Pool([(identifier, "a" * 16, "run", None, None)]), "authorized-run", None, 0, 1
        )
    assert result["status"] == "available" and result["next_cursor"] == 1
    assert result["total"] == 2 and seen == [None, "second"]
    serialized = json.dumps(result)
    assert "DO_NOT_EXPORT" not in serialized and "0001234" not in serialized
    assert "SERVER_SECRET" not in serialized


@pytest.mark.parametrize("mode", ["foreign_trace", "cursor_loop", "oversize", "unavailable"])
async def test_bad_native_read_is_bounded_and_cannot_escape_trace_filter(tmp_path, mode):
    identifier = uuid4().hex

    def handler(request):
        if mode == "oversize":
            return httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))
        if mode == "unavailable":
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "data": [row(uuid4().hex if mode == "foreign_trace" else identifier, 1)],
                "meta": {"cursor": "repeat" if mode == "cursor_loop" else None},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        reader = LangfuseRunClient(http, config(tmp_path))
        state, rows = await reader.trace(identifier)
    assert state in {"truncated", "incomplete", "unavailable"}
    if mode == "foreign_trace":
        assert rows == []


async def test_unregistered_run_is_explicitly_legacy_and_never_searches_langfuse(tmp_path):
    def handler(request):
        pytest.fail("Unregistered historical trace must not be queried as native")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        reader = LangfuseRunClient(http, config(tmp_path))
        result = await reader.read(Pool([]), "authorized-run", uuid4().hex, 0, 10)
    assert result["source"] == "legacy-tempo" and result["status"] == "retired"


async def test_invalid_native_identifier_never_queries(tmp_path):
    def handler(request):
        pytest.fail("Invalid identifier must not reach the platform")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        reader = LangfuseRunClient(http, config(tmp_path))
        assert await reader.trace("../private") == ("unavailable", [])


async def test_missing_end_is_unknown_duration_and_usage_summary_covers_all_pages(tmp_path):
    identifier = uuid4().hex
    records = [
        {
            **row(identifier, i),
            "type": "GENERATION",
            "endTime": None if i == 1 else "2026-10-04T12:00:01Z",
            "usageDetails": {"input": 10, "output": 20},
        }
        for i in (1, 2)
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": records}))
    ) as http:
        reader = LangfuseRunClient(http, config(tmp_path))
        result = await reader.read(
            Pool([(identifier, "a" * 16, "run", None, None)]), "authorized-run", None, 0, 1
        )
    assert result["spans"][0]["duration_ms"] is None
    assert result["explanation"]["unique_generations"] == 2 and result["next_cursor"] == 1
    assert result["explanation"]["known_token_subtotal"] == 60
    assert result["explanation"]["actual_total_tokens"] is None and result["status"] == "incomplete"

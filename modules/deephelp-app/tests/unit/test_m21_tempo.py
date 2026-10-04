import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from deephelp_app.adapters.tempo import LIMIT, TempoClient


@pytest.mark.parametrize("code", [404, 500])
async def test_backend_failure_is_explicit_missing_diagnostic(code):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(code))
    ) as api:
        result = await TempoClient(api, "http://private").read("1" * 32, 0, 10)
        assert result["status"] == "unavailable" and result["spans"] == []


async def test_bounded_result_and_invalid_identifier_never_queries():
    calls = []

    def handle(req):
        calls.append(req)
        return httpx.Response(200, content=b" " * (LIMIT + 1))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as api:
        backend = TempoClient(api, "http://private")
        assert (await backend.read("../private", 0, 10))["status"] == "unavailable"
        assert calls == []
        assert (await backend.read("1" * 32, 0, 10))["status"] == "truncated"


async def test_trace_projection_filters_other_trace_and_private_attributes():
    def span(trace_id, span_id):
        return dict(
            traceId=trace_id,
            spanId=span_id,
            name="stage.X",
            startTimeUnixNano="100",
            endTimeUnixNano="200",
            attributes=[
                {"key": "password", "value": {"stringValue": "private"}},
                {"key": "deephelp.status", "value": {"stringValue": "completed"}},
            ],
        )

    payload = {
        "batches": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            span("1" * 32, "2" * 16),
                            span("1" * 32, "3" * 16),
                            span("4" * 32, "5" * 16),
                        ]
                    }
                ]
            }
        ]
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as api:
        backend = TempoClient(api, "http://private", "http://127.0.0.1:13000")
        first = await backend.read("1" * 32, 0, 1)
        assert first["total"] == 2 and first["next_cursor"] == 1
        assert "private" not in json.dumps(first)
        panes = json.loads(parse_qs(urlsplit(first["grafana_url"]).query)["panes"][0])
        assert panes["trace"]["queries"][0]["queryType"] == "traceId"
        second = await backend.read("1" * 32, first["next_cursor"], 1)
        assert second["next_cursor"] is None
        assert first["spans"][0]["span_id"] != second["spans"][0]["span_id"]

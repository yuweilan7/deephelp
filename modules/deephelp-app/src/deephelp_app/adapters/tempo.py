"""Bounded structural projection of an already-authorized run; never a trace search proxy."""

import base64
import json
import re
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from deephelp_app.adapters.telemetry import safe_attributes
from deephelp_app.domain.errors import ConfigurationError

LIMIT = 2 * 1024 * 1024


def identifier(value: Any, length: int) -> str | None:
    if not isinstance(value, str):
        return None
    if re.fullmatch("[0-9a-f]{" + str(length) + "}", value):
        return value
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError:
        return None
    return raw.hex() if len(raw) * 2 == length else None


def values(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {item.get("key", ""): next(iter(item.get("value", {}).values()), None) for item in rows}


class TempoClient:
    def __init__(
        self, client: httpx.AsyncClient, endpoint: str | None, grafana: str | None = None
    ) -> None:
        self.client, self.endpoint, self.grafana = client, endpoint, grafana
        for address in (endpoint, grafana):
            if address is None:
                continue
            parts = urlsplit(address)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
                or parts.path not in {"", "/"}
            ):
                raise ConfigurationError("Observation URL requires an explicit private base URL")

    async def read(self, trace_id: str | None, cursor: int, page_size: int) -> dict[str, Any]:
        result: dict[str, Any] = {
            "otel_trace_id": trace_id,
            "status": "unavailable",
            "spans": [],
            "next_cursor": None,
            "total": 0,
            "grafana_url": None,
        }
        if not trace_id or not self.endpoint:
            return result
        # The only trace identifier is from the trusted scoped DebugSnapshot.
        if not re.fullmatch(r"[0-9a-f]{32}", trace_id):
            return result
        try:
            async with self.client.stream(
                "GET",
                self.endpoint.rstrip("/") + "/api/traces/" + trace_id,
                headers={"Accept": "application/json"},
                timeout=3,
            ) as response:
                if response.status_code != 200:
                    return result
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > LIMIT:
                        return {**result, "status": "truncated"}
                    data.extend(chunk)
            source = json.loads(data)
            spans: list[dict[str, Any]] = []
            for resource in source.get("batches", source.get("resourceSpans", [])):
                service = values(resource.get("resource", {}).get("attributes", [])).get(
                    "service.name", "unknown"
                )
                for group in resource.get(
                    "scopeSpans", resource.get("instrumentationLibrarySpans", [])
                ):
                    for span in group.get("spans", []):
                        if identifier(span.get("traceId"), 32) != trace_id:
                            continue
                        start = int(span.get("startTimeUnixNano", 0))
                        end = int(span.get("endTimeUnixNano", 0))
                        spans.append(
                            {
                                "span_id": identifier(span.get("spanId"), 16),
                                "parent_span_id": identifier(span.get("parentSpanId"), 16),
                                "service": str(service)[:128],
                                "name": str(span.get("name", "unknown"))[:128],
                                "start_unix_ns": str(start),
                                "duration_ms": max(0, end - start) / 1_000_000,
                                "attributes": safe_attributes(values(span.get("attributes", []))),
                                "links": [
                                    {
                                        "trace_id": identifier(link.get("traceId"), 32),
                                        "span_id": identifier(link.get("spanId"), 16),
                                    }
                                    for link in span.get("links", [])[:16]
                                ],
                            }
                        )
            spans.sort(key=lambda row: (int(row["start_unix_ns"]), row["span_id"] or ""))
            if len(spans) > 20000:
                return {**result, "status": "truncated"}
            query = urlencode(
                {
                    "schemaVersion": "1",
                    "panes": json.dumps(
                        {
                            "trace": {
                                "datasource": "deephelp-tempo",
                                "queries": [
                                    {"refId": "A", "queryType": "traceId", "query": trace_id}
                                ],
                                "range": {"from": "now-24h", "to": "now"},
                            }
                        },
                        separators=(",", ":"),
                    ),
                }
            )
            return {
                **result,
                "status": "available" if spans else "unavailable",
                "spans": spans[cursor : cursor + page_size],
                "next_cursor": cursor + page_size if cursor + page_size < len(spans) else None,
                "total": len(spans),
                "grafana_url": self.grafana.rstrip("/") + "/explore?" + query
                if self.grafana
                else None,
            }
        except httpx.HTTPError, ValueError, TypeError, AttributeError, KeyError:
            return result

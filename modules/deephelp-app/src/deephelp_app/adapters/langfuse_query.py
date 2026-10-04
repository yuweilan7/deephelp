"""Bounded native projection, reachable only with a server-registered scoped run."""

import asyncio
import json
import re
from datetime import datetime
from typing import Any

import httpx

from deephelp_app.adapters.native_observation import configuration
from deephelp_app.adapters.telemetry import safe_attributes
from deephelp_app.adapters.tempo import LIMIT, TempoClient
from deephelp_app.application.debug import sanitize


class LangfuseRunClient:
    def __init__(self, client: httpx.AsyncClient, path: str, legacy: TempoClient) -> None:
        self.client, self.config, self.legacy = client, configuration(path), legacy

    async def trace(self, trace_id: str) -> tuple[str, list[dict[str, Any]]]:
        if not re.fullmatch(r"[0-9a-f]{32}", trace_id):
            return "unavailable", []
        rows: list[dict[str, Any]] = []
        continuation: str | None = None
        size = 0
        try:
            async with asyncio.timeout(5):
                while True:
                    params = {
                        "traceId": trace_id,
                        "limit": "200",
                        "fields": "core,basic,io,usage,metadata,model,metrics",
                    }
                    if continuation:
                        params["cursor"] = continuation
                    async with self.client.stream(
                        "GET",
                        self.config["host"].rstrip("/") + "/api/public/v2/observations",
                        params=params,
                        auth=(self.config["public_key"], self.config["secret_key"]),
                        timeout=3,
                    ) as response:
                        if response.status_code != 200:
                            return "unavailable", []
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > LIMIT:
                                return "truncated", rows
                            data.extend(chunk)
                    body = json.loads(data)
                    batch = body["data"]
                    if not isinstance(batch, list) or any(r["traceId"] != trace_id for r in batch):
                        return "incomplete", []
                    rows.extend(batch)
                    if len(rows) > 20000:
                        return "truncated", rows[:20000]
                    new_cursor = body.get("meta", {}).get("cursor")
                    if not new_cursor:
                        return "available" if rows else "unavailable", rows
                    if new_cursor == continuation:
                        return "incomplete", rows
                    continuation = new_cursor
        except httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError:
            return "unavailable", []

    async def read(
        self,
        pool: Any,
        run_id: str,
        legacy_id: str | None,
        cursor: int,
        page_size: int,
    ) -> dict[str, Any]:
        base: dict[str, Any] = {
            "source": "langfuse",
            "status": "unavailable",
            "spans": [],
            "total": 0,
            "next_cursor": None,
            "grafana_url": None,
            "langfuse_url": None,
            "associations": [],
        }
        try:
            async with asyncio.timeout(1), pool.acquire() as conn, conn.cursor() as db:
                await db.execute(
                    "SELECT trace_id,span_id,kind,predecessor_trace_id,predecessor_span_id "
                    "FROM dh_m28_observation_runs WHERE run_id=%s "
                    "ORDER BY created_at,trace_id,span_id LIMIT 101",
                    (run_id,),
                )
                bindings = await db.fetchall()
                await conn.rollback()
        except Exception:
            return base
        if not bindings:
            return {
                **await self.legacy.read(legacy_id, cursor, page_size),
                "source": "legacy-tempo",
            }
        if len(bindings) > 100:
            return {**base, "status": "truncated"}
        spans: list[dict[str, Any]] = []
        traces = list(dict.fromkeys(row[0] for row in bindings))
        status = "available"
        size = 0
        try:
            async with asyncio.timeout(8):
                for trace_id in traces:
                    state, rows = await self.trace(trace_id)
                    if state != "available":
                        status = "incomplete" if spans else state
                    size += len(json.dumps(rows).encode())
                    if size > LIMIT:
                        status = "truncated"
                        break
                    spans.extend(self.project(row) for row in rows)
        except TimeoutError, ValueError, KeyError, TypeError, AttributeError:
            status = "incomplete"
        spans.sort(key=lambda row: (row["start_unix_ns"], row["span_id"]))
        if any(row["incomplete"] for row in spans) and status == "available":
            status = "incomplete"
        return {
            **base,
            "status": status,
            "otel_trace_id": traces[0],
            "spans": spans[cursor : cursor + page_size],
            "total": len(spans),
            "next_cursor": cursor + page_size if cursor + page_size < len(spans) else None,
            "associations": [
                {
                    "trace_id": r[0],
                    "span_id": r[1],
                    "kind": r[2],
                    "predecessor_trace_id": r[3],
                    "predecessor_span_id": r[4],
                }
                for r in bindings
            ],
            "langfuse_url": self.config["host"].rstrip("/")
            + "/project/"
            + str(rows[0]["projectId"])
            + "/traces/"
            + traces[-1]
            if spans and rows and re.fullmatch(r"[A-Za-z0-9_-]+", str(rows[0].get("projectId", "")))
            else None,
        }

    def project(self, row: dict[str, Any]) -> dict[str, Any]:
        metadata = row.get("metadata") or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        start = datetime.fromisoformat(row["startTime"].replace("Z", "+00:00"))
        content = sanitize(
            {"input": row.get("input"), "output": row.get("output")},
            bytes.fromhex(self.config["redaction_key"]),
        )
        return {
            "span_id": row["id"],
            "trace_id": row["traceId"],
            "parent_span_id": row.get("parentObservationId"),
            "service": metadata.get("service.name", "unknown"),
            "name": row["name"],
            "start_unix_ns": str(int(start.timestamp() * 1_000_000_000)),
            "duration_ms": (row.get("latency") or 0) * 1000,
            "attributes": safe_attributes(metadata),
            "links": [],
            "type": row["type"],
            "model": row.get("providedModelName", row.get("model")),
            "usage": row.get("usageDetails"),
            "input": content["input"],
            "output": content["output"],
            "version": row.get("version"),
            "incomplete": row.get("endTime") is None,
        }

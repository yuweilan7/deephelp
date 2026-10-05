"""Historical frozen evidence -> native v4 experiments; never execute a business row."""

import asyncio
import importlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from langfuse import Langfuse
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.id_generator import IdGenerator

from deephelp_app.adapters.releases.asset_integrity import file_digest
from deephelp_app.application.corpus import digest
from deephelp_app.application.dense import atomic_json, load_json
from deephelp_app.domain.errors import ConfigurationError
from deephelp_tools.evaluation.quality.core import audit, expected
from deephelp_tools.evaluation.quality.evaluation_approval import approval_cases

_ids: ContextVar[tuple[str, str]] = ContextVar("m29_import_ids")
FROM = "2020-01-01T00:00:00Z"


def json_value(value: Any) -> Any:
    """v4 IO fields contain serialized JSON; decode only at the read boundary."""
    return json.loads(value) if isinstance(value, str) else value


class ImportIds(IdGenerator):
    """Standard OTel public ID seam; an uncertain resend replaces the same span."""

    def generate_trace_id(self) -> int:
        return int(_ids.get()[0], 16)

    def generate_span_id(self) -> int:
        return int(_ids.get()[1], 16)


def plan_import(
    report_path: Path,
    data: Path,
    manifest: Path,
    case_ids: list[str],
    modes: list[str],
) -> dict[str, Any]:
    if report_path.stat().st_size > 8_000_000:
        raise ConfigurationError("Historical report exceeds 8MB")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    cases, frozen = audit(data, manifest)
    if (
        report.get("format") != "m17-evaluation-v1"
        or report.get("status") not in {"PASS", "REJECTED"}
        or report["dataset"]["data_digest"] != frozen["data_digest"]
        or report["dataset"]["manifest_digest"] != frozen["manifest_digest"]
        or not 1 <= len(case_ids) <= 20
        or len(set(case_ids)) != len(case_ids)
        or not modes
        or len(set(modes)) != len(modes)
        or set(modes) - set(report["routes"])
        or set(case_ids) - set(report["selected_case_ids"])
    ):
        raise ConfigurationError("Select distinct complete cases from the matching frozen report")
    source = file_digest(report_path)
    selected = {c.case_id: c for c in cases if c.case_id in case_ids}
    if set(selected) != set(case_ids):
        raise ConfigurationError("A selected case is absent from the frozen corpus")
    summary = {
        "historical_import": True,
        "model_calls": 0,
        "source_report_sha256": source,
        "source_report_status": report["status"],
        "dataset": frozen,
        "provenance": report["provenance"],
        "configuration": report["configuration"],
        "experiment_fingerprint": report["experiment_fingerprint"],
        "release_gate": report["release_gate"],
        "selected_case_ids": case_ids,
        "selected_modes": modes,
        "original_route_metrics": {
            m: {
                k: v
                for k, v in report["routes"][m].items()
                if k in {"metrics", "capabilities", "checks", "split_metrics"}
            }
            for m in modes
        },
        "note": "Historical subset import, not new acceptance, training or enterprise quality",
    }
    rows: list[dict[str, Any]] = []
    for mode in modes:
        original = report["routes"][mode]["rows"]
        keys = [(r["case_id"], r["turn_id"]) for r in original]
        if len(keys) != len(set(keys)):
            raise ConfigurationError("Duplicate source report row")
        indexed = dict(zip(keys, original, strict=True))
        for cid in case_ids:
            case = selected[cid]
            for index, turn in enumerate(case.turns):
                row = indexed.get((cid, turn.turn_id))
                if row is None or row["text"] != turn.text or row["split"] != case.split:
                    raise ConfigurationError("Incomplete or changed historical conversation")
                if row["score"]["gold"] != expected(turn):
                    raise ConfigurationError("Source gold does not match frozen semantic facts")
                rows.append(
                    {
                        "key": [mode, cid, turn.turn_id],
                        "input": {
                            "text": turn.text,
                            "history": [t.text for t in case.turns[:index]],
                        },
                        "expected": {
                            "label": turn.label,
                            "entities": turn.entities,
                            "gold": row["score"]["gold"],
                        },
                        "metadata": {
                            "split": case.split,
                            "source_group": case.source_group,
                            "variant_group": case.variant_group,
                            "case_id": cid,
                            "turn_id": turn.turn_id,
                            "mode": mode,
                            "frozen_data_digest": frozen["data_digest"],
                            "synthetic": True,
                        },
                        "output": row,
                        "scores": {
                            **row["score"]["checks"],
                            "completed": row["score"]["completed"],
                            "hard_gate": not row["score"]["hard_failures"],
                        },
                    }
                )
    if approvals := report.get("approvals"):
        gold = approval_cases()
        operations = {r["case"]: r for r in gold["operations"]}
        if (
            approvals["dataset_digest"] != digest(gold)
            or {r["case"] for r in approvals["rows"]} != set(operations)
            or sorted(r["case"] for r in approvals["unauthorized"])
            != sorted(gold["unauthorized_cases"])
            or len(approvals["rows"]) != len(operations)
        ):
            raise ConfigurationError("Incomplete approval / unauthorized source coverage")
        summary["approvals"] = {
            k: approvals[k] for k in ("dataset_digest", "metrics", "release_gate")
        }
        for row in approvals["rows"]:
            checks = {"case_gate": approvals["release_gate"]["checks"][row["case"]]}
            for key in ("effects", "execute_calls", "query_calls"):
                checks[key] = row[key]
            if "replay_unchanged" in row:
                checks["replay_unchanged"] = row["replay_unchanged"]
            rows.append(
                {
                    "key": ["approval", row["case"]],
                    "input": {"case": row["case"]},
                    "expected": operations[row["case"]],
                    "metadata": {"split": "approval-regression", "synthetic": True},
                    "output": row,
                    "scores": checks,
                }
            )
        for row in approvals["unauthorized"]:
            rows.append(
                {
                    "key": ["unauthorized", row["case"]],
                    "input": {"case": row["case"]},
                    "expected": {"rejected": True, "unchanged": True},
                    "metadata": {"split": "authorization-regression", "synthetic": True},
                    "output": row,
                    "scores": {
                        "rejected": row["rejected"],
                        "zero_effect": row["before"] == row["after"],
                    },
                }
            )
    if len(rows) > 100:
        raise ConfigurationError("Select a bounded historical subset (maximum 100 rows)")
    identity = digest({"wire_format": "exact-json-v1", "summary": summary, "rows": rows})
    for row in rows:
        key = digest([identity, row["key"]])
        row.update(item_id="dh-m29-" + key, trace_id=key[:32], observation_id=key[32:48])
        row["metadata"].update(
            source_report_sha256=source,
            historical_import=True,
            original_key=row["key"],
            row_sha256=digest(row["output"]),
        )
        # PG/JavaScript JSON numbers may round historical timing/ratios. Keep the
        # exact source JSON as text; no approximate equality or edited old report.
        row["output"] = {"source_row_json": json.dumps(row["output"], ensure_ascii=False)}
    return {
        "format": "m29-import-v1",
        "identity": identity,
        "dataset_name": "deephelp-m29-" + identity[:20],
        "experiment_id": identity[:32],
        "experiment_name": "history-" + identity[:20],
        "summary": summary,
        "rows": rows,
    }


class Platform:
    """Server credentials stay here; GETs and deterministic writes are bounded."""

    def __init__(self, client: httpx.AsyncClient, config: dict[str, Any]) -> None:
        self.client, self.config = client, config

    async def request(
        self, method: str, path: str, *, idempotent: bool = True, **kwargs: Any
    ) -> Any:
        attempts = 3 if idempotent else 1
        for attempt in range(attempts):
            try:
                reply = await self.client.request(
                    method,
                    self.config["host"].rstrip("/") + "/api/public/" + path,
                    auth=(self.config["public_key"], self.config["secret_key"]),
                    timeout=5,
                    **kwargs,
                )
                if reply.status_code == 404:
                    return None
                if reply.status_code in {429, 500, 502, 503, 504} and attempt < attempts - 1:
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                reply.raise_for_status()
                return reply.json()
            except httpx.TransportError:
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(0.2 * (attempt + 1))
        raise ConfigurationError("Platform retry exhausted")

    async def verify(self, row: dict[str, Any], plan: dict[str, Any]) -> bool:
        item = await self.request("GET", "dataset-items/" + row["item_id"])
        if item is None:
            return False
        if any(
            item.get(k) != v
            for k, v in {
                "input": row["input"],
                "expectedOutput": row["expected"],
                "metadata": row["metadata"],
                "datasetName": plan["dataset_name"],
            }.items()
        ):
            raise ConfigurationError("Platform item conflicts with immutable import")
        body = await self.request(
            "GET",
            "experiment-items",
            params={
                "fromStartTime": FROM,
                "experimentId": plan["experiment_id"],
                "traceId": row["trace_id"],
                "fields": "core,io,dataset,scores,itemMetadata",
                "limit": 100,
            },
        )
        found = [r for r in body["data"] if r["traceId"] == row["trace_id"]]
        if not found:
            return False
        if len(found) != 1:
            raise ConfigurationError("Duplicate imported experiment row")
        observed = found[0]
        if (
            observed["id"] != row["observation_id"]
            or not observed.get("endTime")
            or json_value(observed.get("output")) != row["output"]
            or json_value(observed.get("input")) != row["input"]
            or json_value(observed.get("expectedOutput")) != row["expected"]
            or observed.get("experimentItemId") != row["item_id"]
        ):
            return False
        expected_scores = {
            digest([plan["identity"], row["key"], name]): (name, value)
            for name, value in row["scores"].items()
            if value is not None
        }
        scores = await self.request(
            "GET",
            "v3/scores",
            params={
                "observationId": row["observation_id"],
                "traceId": row["trace_id"],
                "fields": "subject,details",
                "limit": 100,
            },
        )
        actual = {s["id"]: (s["name"], s["value"]) for s in scores["data"]}
        return actual == expected_scores


def dataset_metadata(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "historical_import": True,
        "source_report_sha256": plan["summary"]["source_report_sha256"],
        "expected_rows": len(plan["rows"]),
        "summary_json": json.dumps(plan["summary"], ensure_ascii=False),
    }


@contextmanager
def import_lock(path: Path) -> Iterator[None]:
    """OS lock releases on process exit, so interrupted imports remain resumable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ConfigurationError("Another importer owns this state") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


async def upload(plan: dict[str, Any], state_path: Path, platform: Platform) -> dict[str, Any]:
    with import_lock(state_path):
        return await _upload(plan, state_path, platform)


async def _upload(plan: dict[str, Any], state_path: Path, platform: Platform) -> dict[str, Any]:
    binding = digest([plan["identity"], platform.config["host"], platform.config["public_key"]])
    state: dict[str, Any] = (
        load_json(state_path)
        if state_path.exists()  # noqa: ASYNC240 -- bounded state, synchronous atomic ownership
        else {"binding": binding, "identity": plan["identity"], "rows": {}, "status": "PENDING"}
    )
    if state["binding"] != binding:
        raise ConfigurationError("Upload state belongs to another source/project/selection")
    state["status"] = "RUNNING"
    state.pop("error_type", None)
    atomic_json(state_path, state)
    sdk: Langfuse | None = None
    try:
        async with asyncio.timeout(600):
            dataset = await platform.request(
                "GET", "v2/datasets/" + quote(plan["dataset_name"], safe="")
            )
            if dataset is None:
                dataset = await platform.request(
                    "POST",
                    "v2/datasets",
                    json={
                        "name": plan["dataset_name"],
                        "metadata": dataset_metadata(plan),
                        "description": "Historical M17 subset; no new model/business execution",
                    },
                )
            if dataset["metadata"] != dataset_metadata(plan):
                raise ConfigurationError("Platform dataset source metadata changed")
            state["dataset_id"] = dataset["id"]
            state["dataset_url"] = (
                platform.config["host"].rstrip("/")
                + "/project/"
                + dataset["projectId"]
                + "/datasets/"
                + dataset["id"]
            )
            state["experiment_url"] = (
                platform.config["host"].rstrip("/")
                + "/project/"
                + dataset["projectId"]
                + "/experiments/results?baseline="
                + plan["experiment_id"]
            )
            for row in plan["rows"]:
                identity = row["item_id"]
                if not await platform.verify(row, plan):
                    # A writer owns an isolated CLI process. Read-only recovery never
                    # initializes SDK queues, including after a cancelled writer.
                    if sdk is None:
                        sdk = Langfuse(
                            public_key=platform.config["public_key"],
                            secret_key=platform.config["secret_key"],
                            base_url=platform.config["host"],
                            timeout=3,
                            flush_at=8,
                            flush_interval=0.25,
                            environment="m29-historical-import",
                            tracer_provider=TracerProvider(id_generator=ImportIds()),
                        )
                    await platform.request(
                        "POST",
                        "dataset-items",
                        json={
                            "datasetName": plan["dataset_name"],
                            "id": identity,
                            "input": row["input"],
                            "expectedOutput": row["expected"],
                            "metadata": row["metadata"],
                        },
                    )
                    token = _ids.set((row["trace_id"], row["observation_id"]))
                    try:
                        with sdk.start_as_current_observation(
                            name="historical-" + str(row["key"][0]),
                            input=row["input"],
                            output=row["output"],
                            metadata=row["metadata"],
                            version=plan["summary"]["source_report_sha256"],
                        ) as observation:
                            trace.get_current_span().set_attributes(
                                {
                                    "langfuse.experiment.id": plan["experiment_id"],
                                    "langfuse.experiment.name": plan["experiment_name"],
                                    "langfuse.experiment.dataset.id": dataset["id"],
                                    "langfuse.experiment.item.id": identity,
                                    "langfuse.experiment.item.root_observation_id": observation.id,
                                    "langfuse.experiment.item.expected_output": json.dumps(
                                        row["expected"]
                                    ),
                                }
                            )
                    finally:
                        _ids.reset(token)
                    for name, value in row["scores"].items():
                        if value is not None:
                            sdk.create_score(
                                trace_id=row["trace_id"],
                                observation_id=row["observation_id"],
                                score_id=digest([plan["identity"], row["key"], name]),
                                name=name,
                                value=float(value),
                                data_type="BOOLEAN" if isinstance(value, bool) else "NUMERIC",
                                comment="Historical assertion; M18 gate authoritative",
                            )
                    await asyncio.to_thread(sdk.flush)
                    async with asyncio.timeout(60):
                        while not await platform.verify(row, plan):  # noqa: ASYNC110 -- remote readback
                            await asyncio.sleep(1)
                state["rows"][identity] = "VERIFIED"
                atomic_json(state_path, state)
            if set(state["rows"]) != {r["item_id"] for r in plan["rows"]}:
                raise ConfigurationError("Upload state contains unexpected rows")
            items = await platform.request(
                "GET", "dataset-items", params={"datasetName": plan["dataset_name"], "limit": 100}
            )
            experiments = await platform.request(
                "GET",
                "experiment-items",
                params={"fromStartTime": FROM, "experimentId": plan["experiment_id"], "limit": 100},
            )
            if (
                len(items["data"]) != len(plan["rows"])
                or {r["id"] for r in items["data"]} != {r["item_id"] for r in plan["rows"]}
                or len(experiments["data"]) != len(plan["rows"])
                or {r["id"] for r in experiments["data"]}
                != {r["observation_id"] for r in plan["rows"]}
            ):
                raise ConfigurationError(
                    "Platform import coverage differs from the frozen selection"
                )
            state.update(
                status="COMPLETE",
                expected_rows=len(plan["rows"]),
                model_calls=0,
                source_report_sha256=plan["summary"]["source_report_sha256"],
            )
    except BaseException as exc:
        state.update(
            status="CANCELLED" if isinstance(exc, asyncio.CancelledError) else "PARTIAL",
            error_type=type(exc).__name__,
        )
        raise
    finally:
        atomic_json(state_path, state)
        if sdk is not None:
            await asyncio.shield(asyncio.to_thread(sdk.shutdown))
    return state

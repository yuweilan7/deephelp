"""Audit/frozen same-data incremental comparison with partial evidence on failure."""

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from deephelp_app.app import create_app
from deephelp_app.asset_integrity import evaluation_provenance as provenance
from deephelp_app.business_catalog import business_config, business_registry
from deephelp_app.corpus import digest
from deephelp_app.domain.models import ErrorCode, ResponseEnvelope
from deephelp_app.errors import ConfigurationError
from deephelp_app.evaluation.core import (
    DATA,
    MANIFEST,
    MODES,
    audit,
    capability_eligible,
    file_digest,
    metrics,
    release_gate,
    score,
    targeted_gate,
)
from deephelp_app.evaluation.evaluation_approval import approval_cases, evaluate_approvals
from deephelp_app.evaluation.evaluation_runtime import EvaluationAssembly
from deephelp_app.evaluation.mvp_acceptance import api_client
from deephelp_app.fasttext_runtime import FastTextClassifier, pointer_manifest
from deephelp_app.local_paths import local_path
from deephelp_app.mvp_runtime import BudgetSession, LiveAssembly, LocalAuth, validate_control_paths
from deephelp_app.probes.usage import api_records, summarize_usage
from deephelp_app.settings import Settings
from deephelp_app.tool_gateway import process_alive
from deephelp_app.trace import MemoryTrace


def read_report(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return value


def compare_reports(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    if baseline.get("format") != "m17-evaluation-v1" or baseline.get("status") != "PASS":
        raise ConfigurationError("Baseline must be a successful frozen evaluation report")
    if (
        current["experiment_fingerprint"] != baseline["experiment_fingerprint"]
        or current["live"] != baseline["live"]
        or current["selected_case_ids"] != baseline["selected_case_ids"]
    ):
        raise ConfigurationError("Baseline data/code/models/configuration/budget/selection differ")
    checks: dict[str, bool] = {}
    scope: dict[str, Any] = {}
    if set(current["routes"]) != set(baseline["routes"]):
        raise ConfigurationError("Baseline route scope differs")
    for mode in current["routes"]:
        old = {r["turn_id"]: r for r in baseline["routes"][mode]["rows"]}
        rows = current["routes"][mode]["rows"]
        # A stateless ablation cannot repeat an accidental inference of a prior turn.
        # Keep its raw mistakes in metrics; compare only capabilities it actually enables.
        events = current["routes"][mode].get("capabilities", {}).get("events", True)
        prior_events = baseline["routes"][mode].get("capabilities", {}).get("events", True)
        eligible = [r for r in rows if capability_eligible(current["routes"][mode], r)]
        prior = [r for r in old.values() if capability_eligible(baseline["routes"][mode], r)]
        checks[mode + ":capability_scope"] = events == prior_events and {
            r["turn_id"] for r in eligible
        } == {r["turn_id"] for r in prior}
        scope[mode] = {
            "compared_turn_ids": [r["turn_id"] for r in eligible],
            "diagnostic_only_turn_ids": [r["turn_id"] for r in rows if r not in eligible],
            "reason": "automatic followup/multiple require disabled events"
            if not events
            else "all turns",
            "raw_completed": sum(r["score"]["completed"] for r in rows),
            "baseline_raw_completed": sum(r["score"]["completed"] for r in old.values()),
        }
        checks[mode + ":coverage"] = set(old) == {r["turn_id"] for r in rows}
        checks[mode + ":completed_no_regression"] = sum(
            r["score"]["completed"] for r in eligible
        ) >= sum(r["score"]["completed"] for r in prior)
        checks[mode + ":no_new_hard_failures"] = all(not r["score"]["hard_failures"] for r in rows)
        checks[mode + ":case_no_regression"] = (
            current["routes"][mode]["metrics"]["case_completion_rate"]["numerator"]
            >= baseline["routes"][mode]["metrics"]["case_completion_rate"]["numerator"]
        )
    if current.get("approvals") or baseline.get("approvals"):
        new = current.get("approvals") or {}
        old_approval = baseline.get("approvals") or {}
        checks["approval_scope"] = bool(new and old_approval) and (
            new.get("dataset_digest") == old_approval.get("dataset_digest")
            and new.get("selected_case_ids") == old_approval.get("selected_case_ids")
        )
        checks["approval_safety"] = new.get("release_gate", {}).get("accepted") is True
    return {
        "accepted": all(checks.values()),
        "checks": checks,
        "comparison_scope": scope,
        "baseline_digest": digest(baseline),
        "note": "nondeterministic live responses; same frozen experiment",
    }


def select_scope(args: argparse.Namespace, cases: list[Any]) -> tuple[list[Any], list[str]]:
    full = getattr(args, "full_evaluation", False)
    ids = list(getattr(args, "case", None) or [])
    modes = list(getattr(args, "mode", None) or [])
    if full and (ids or modes):
        raise ConfigurationError("--full-evaluation cannot be combined with --case/--mode")
    if (
        args.live
        and not full
        and (not ids or not modes or args.all_cases or getattr(args, "approvals", False))
    ):
        raise ConfigurationError(
            "Targeted live requires --case and --mode; "
            "full live/approvals require --full-evaluation"
        )
    if len(ids) != len(set(ids)) or not set(ids) <= {c.case_id for c in cases}:
        raise ConfigurationError("Duplicate or unknown evaluation case ID")
    if len(modes) != len(set(modes)) or not set(modes) <= set(MODES):
        raise ConfigurationError("Duplicate or unknown evaluation mode")
    # Whole conversations preserve prior turns; IDs can include non-default samples.
    selected = (
        [c for c in cases if c.case_id in ids]
        if ids
        else [c for c in cases if not args.live or args.all_cases or c.live_sample]
    )
    if getattr(args, "split", None):
        selected = [c for c in selected if c.split == args.split]
    if not selected or ids and set(ids) != {c.case_id for c in selected}:
        raise ConfigurationError("Empty scope or selected cases conflict with --split")
    return selected, modes or list(MODES)


async def run(args: argparse.Namespace) -> int:
    cases, data_summary = audit(Path(args.data), Path(args.manifest))
    selected, modes = select_scope(args, cases)
    business = bool(data_summary.get("business_catalog"))
    output = local_path(args.output)
    if output.exists():
        raise ConfigurationError("Use a new immutable evaluation report path")
    controls = [
        Path(args.auth),
        Path(args.budget_state),
        Path(args.pointer),
        Path(args.providers),
        Path(args.fasttext_pointer),
        Path(args.data),
        Path(args.manifest),
    ]
    if args.baseline:
        controls.append(Path(args.baseline))
    validate_control_paths(controls, output=output)
    report: dict[str, Any] = {
        "format": "m17-evaluation-v1",
        "status": "PENDING",
        "live": args.live,
        "stage": args.stage,
        "acceptance_scope": "full_evaluation"
        if getattr(args, "full_evaluation", False)
        else "targeted"
        if getattr(args, "mode", None) or getattr(args, "case", None)
        else "offline_full",
        "dataset": data_summary,
        "selected_case_ids": [c.case_id for c in selected],
        "routes": {},
        "provenance": provenance(),
        "interpretation": "live services/synthetic business"
        if args.live
        else "offline deterministic semantic/SOP adapters; not model quality or live acceptance",
        "test_used_for_tuning": False,
    }
    configuration: dict[str, Any] = {
        "data": data_summary["data_digest"],
        "code": report["provenance"]["package_digest"],
        "evaluation_tooling": report["provenance"]["tooling_digest"],
        "timeout_seconds": 90,
        "attempts": 40,
        "retries": 2,
        "top_k": 3,
        "prompt": "m12-fallback-v2",
        "sop": business_registry().snapshot_hash if business else "bundled_registry",
        "reply_polish": False,
        "mode_order": modes,
        "candidate_gates": "M12 dev-frozen hybrid; dense takeover uncalibrated",
    }
    if getattr(args, "approvals", False):
        configuration["approval_dataset"] = digest(approval_cases())
    gate = None
    if args.live:
        auth = LocalAuth(Path(args.auth))
        configuration.update(
            providers=file_digest(Path(args.providers)),
            strong_model=file_digest(Path("modules/deephelp-app/event-judge.example.json")),
            retrieval_pointer=file_digest(Path(args.pointer)),
            cascade_policy=file_digest(
                Path(__file__).parents[1] / "assets/runtime/m12_policy.json"
            ),
            fasttext=FastTextClassifier(
                pointer_manifest(Path(args.fasttext_pointer))
            ).manifest.model_dump(mode="json")
            if "fasttext" in modes
            else None,
        )
        token, identity = auth.rows[0]
        from deephelp_app.learning.event_replay import envelope

        if identity != envelope("x", "x").identity:
            raise ConfigurationError("Evaluation needs the synthetic-user-a fixture identity")
        gate = BudgetSession(Path(args.budget_state))
    else:
        from deephelp_app.learning.event_replay import envelope

        token, identity = "offline-evaluation", envelope("x", "x").identity
    report["configuration"] = configuration
    report["experiment_fingerprint"] = digest(configuration)

    def save() -> None:
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if len(encoded.encode()) > 256 * 1024**2:
            raise ConfigurationError("Evaluation evidence exceeds 256MiB")
        temporary = output.with_suffix(output.suffix + ".tmp")
        try:
            temporary.write_text(encoded, encoding="utf-8")
            for attempt in range(5):
                try:
                    temporary.replace(output)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.02)
        finally:
            temporary.unlink(missing_ok=True)

    nonce = uuid4().hex[:16]
    journal = None
    try:
        if business:
            journal_path = output.with_suffix(".rows.jsonl")
            journal = journal_path.open("x", encoding="utf-8")
            report["row_journal"] = str(journal_path)
        if gate:
            gate.open()
        async with asyncio.timeout(args.timeout):
            for mode in modes:
                collection = "dh_m10_events_m17_" + nonce + "_" + mode
                live = (
                    LiveAssembly(
                        Path.cwd(),
                        Path(args.providers),
                        Path(args.pointer),
                        event_collection=collection,
                        fasttext_pointer=Path(args.fasttext_pointer)
                        if mode == "fasttext"
                        else None,
                        mock=business_config() if business else None,
                    )
                    if args.live
                    else None
                )
                if live and business:
                    registry = business_registry()
                    live.sop_registry, live.sop_snapshots = registry, (registry,)
                assembly = EvaluationAssembly(mode, live=live, business=business)
                trace = MemoryTrace()
                app = create_app(
                    Settings(
                        mode="mvp" if args.live else "test",
                        request_timeout=90,
                        child_timeout=30,
                        max_attempts=40,
                        retry_limit=2,
                    ),
                    trace=trace,
                    identity_provider=auth if args.live else lambda request: identity,
                    conversation_factory=assembly.open,
                    budget_factory=gate.request if gate else None,
                )
                route: dict[str, Any] = {
                    "rows": [],
                    "api_observations": [],
                    "checks": {},
                    "capabilities": {
                        "retrieval": "dense" if mode == "rule_dense" else "hybrid",
                        "events": mode in {"memory_event", "fasttext"},
                        "fasttext": "enabled"
                        if args.live and mode == "fasttext"
                        else "not_run_offline"
                        if mode == "fasttext"
                        else "disabled",
                    },
                }
                report["routes"][mode] = route
                try:
                    async with app.router.lifespan_context(app):
                        assert assembly.tools and assembly.retriever and assembly.ledger
                        async with api_client(app, http=True) as client:
                            last_body, last_response = None, None
                            for case in selected:
                                session = "m17-" + nonce + "-" + mode + "-" + case.case_id
                                source_ids: dict[str, list[str]] = {}
                                questions: dict[str, Any] = {}
                                for turn in case.turns:
                                    assembly.retriever.rows.clear()
                                    body = {
                                        "channel": "m17-evaluation",
                                        "session_id": session,
                                        "message_id": uuid4().hex,
                                        "raw_text": turn.text,
                                        "occurred_at": datetime.now(UTC).isoformat(),
                                    }
                                    started = perf_counter()
                                    gateways = [live.gateway, live.judge_gateway] if live else []
                                    previous = [list(g.diagnostics) for g in gateways]
                                    if turn.hint_event:
                                        if turn.hint_event not in questions:
                                            raise ConfigurationError(
                                                "Hint has no authorized prior question"
                                            )
                                        q = questions[turn.hint_event]
                                        body.update(
                                            question_hint=q.question_id,
                                            expected_question_version=q.version,
                                        )
                                    source_ids[turn.turn_id] = [body["message_id"]]
                                    reply = await client.post(
                                        "/converse",
                                        json=body,
                                        headers={"Authorization": "Bearer " + token},
                                    )
                                    response = ResponseEnvelope.model_validate(reply.json())
                                    elapsed = (perf_counter() - started) * 1000
                                    observations = [
                                        record
                                        for g, before in zip(gateways, previous, strict=True)
                                        for record in api_records(
                                            g.diagnostics,
                                            phase=mode,
                                            sample=turn.turn_id,
                                            previous=before,
                                        )
                                    ]
                                    route["api_observations"].extend(observations)
                                    question = await assembly.ledger.question(
                                        identity, session, response.question_id
                                    )
                                    if question:
                                        questions[turn.event_id] = question
                                    records = [
                                        r.model_dump(mode="json")
                                        for r in await assembly.tools.ledger()
                                        if r.call_id in response.tool_call_ids
                                    ]
                                    observation = {
                                        "unresolved_fields": [
                                            f.value for f in question.unresolved_fields
                                        ]
                                        if question
                                        else [],
                                        "entities": {
                                            e.name.value: e.value for e in question.entities
                                        }
                                        if question
                                        else {},
                                        "retrievals": list(assembly.retriever.rows),
                                        "tools": records,
                                        "entity_sources": {
                                            e.name.value: e.source.model_dump(mode="json")
                                            for e in question.entities
                                        }
                                        if question
                                        else {},
                                        "allowed_sources": {
                                            name: [
                                                mid
                                                for prior in case.turns[
                                                    : case.turns.index(turn) + 1
                                                ]
                                                if prior.event_id == turn.event_id
                                                and value in prior.text
                                                for mid in source_ids[prior.turn_id]
                                            ]
                                            for name, value in turn.entities.items()
                                        },
                                    }
                                    row: dict[str, Any] = {
                                        "case_id": case.case_id,
                                        "turn_id": turn.turn_id,
                                        "split": case.split,
                                        "event_id": turn.event_id,
                                        "multi_turn": len(case.turns) > 1,
                                        "expectation": turn.expectation,
                                        "hint_event": turn.hint_event,
                                        "text": turn.text,
                                        "response": response.model_dump(mode="json"),
                                        "observation": observation,
                                        "elapsed_ms": elapsed,
                                        "http_status": reply.status_code,
                                        "api_usage": summarize_usage(
                                            observations, attempts=response.call_counts.model_calls
                                        )
                                        if args.live
                                        else None,
                                    }
                                    row["score"] = score(turn, row["response"], observation)
                                    route["rows"].append(row)
                                    if journal:
                                        journal.write(
                                            json.dumps({"mode": mode, **row}, ensure_ascii=False)
                                            + "\n"
                                        )
                                        journal.flush()
                                    route["checks"][turn.turn_id + ":persisted"] = bool(
                                        question and question.status == response.question_status
                                    )
                                    last_body, last_response = body, response
                                    if not business or (
                                        turn is case.turns[-1] and selected.index(case) % 20 == 0
                                    ):
                                        save()
                                    if response.error and response.error.code in {
                                        ErrorCode.UPSTREAM_UNAVAILABLE,
                                        ErrorCode.UNAUTHENTICATED,
                                        ErrorCode.BUDGET_EXHAUSTED,
                                        ErrorCode.TIMEOUT,
                                        ErrorCode.INTERNAL_ERROR,
                                    }:
                                        raise ConfigurationError(
                                            "Dependency/runtime failure; inspect partial report"
                                        )
                            if last_body and last_response:
                                replay = ResponseEnvelope.model_validate(
                                    (
                                        await client.post(
                                            "/converse",
                                            json=last_body,
                                            headers={"Authorization": "Bearer " + token},
                                        )
                                    ).json()
                                )
                                route["checks"]["zero_call_replay"] = bool(
                                    replay.replayed
                                    and replay.run_id == last_response.run_id
                                    and replay.call_counts.model_calls
                                    == replay.call_counts.tool_calls
                                    == 0
                                )
                        if live:
                            from deephelp_app.cases import MySQLCaseRepository

                            reader = await MySQLCaseRepository.open(Path.cwd())
                            try:
                                assert last_body and last_response
                                stored = await reader.question(
                                    identity,
                                    last_body["session_id"],
                                    last_response.question_id or "none",
                                )
                                route["checks"]["new_mysql_pool_readback"] = bool(
                                    stored and stored.status == last_response.question_status
                                )
                            finally:
                                await reader.aclose()
                        route["metrics"] = metrics(
                            route["rows"],
                            live=args.live,
                            event_enabled=mode in {"memory_event", "fasttext"},
                        )
                        route["split_metrics"] = {
                            split: metrics(
                                [r for r in route["rows"] if r["split"] == split],
                                live=args.live,
                                event_enabled=mode in {"memory_event", "fasttext"},
                            )
                            for split in ("test", "regression")
                        }
                        if live:
                            route["api_usage"] = summarize_usage(
                                route.pop("api_observations"),
                                attempts=route["metrics"]["real_model_attempts"],
                            )
                            route["diagnostics"] = {
                                "primary": live.gateway.diagnostics,
                                "strong": live.judge_gateway.diagnostics,
                            }
                        print(
                            json.dumps(
                                {
                                    "mode": mode,
                                    "turns": len(route["rows"]),
                                    "accuracy": route["metrics"]["accuracy"]["value"],
                                    "completed": route["metrics"]["completion_rate"]["value"],
                                }
                            ),
                            flush=True,
                        )
                finally:
                    if live:
                        route["checks"]["owned_event_collection_deleted"] = (
                            assembly.collection_deleted
                        )
                    if assembly.tools and assembly.tools.pid:
                        route["checks"]["mcp_exited"] = not process_alive(assembly.tools.pid)
                    save()
            if getattr(args, "approvals", False):
                report["approvals"] = await evaluate_approvals(args, output)
            report["release_gate"] = (
                targeted_gate if report["acceptance_scope"] == "targeted" else release_gate
            )(
                report["routes"],
                complete=all(
                    len(route["rows"]) == sum(len(c.turns) for c in selected)
                    for route in report["routes"].values()
                ),
            )
            if business:
                report["release_gate"]["scope"] = {
                    "frozen_conversations": data_summary["independent_cases"],
                    "selected_conversations": len(selected),
                    "selected_messages_per_route": sum(len(c.turns) for c in selected),
                    "business_geometries": data_summary["business_catalog"]["business_geometries"],
                    "synthetic_business": True,
                    "enterprise_quality_validated": False,
                }
            if getattr(args, "approvals", False):
                report["release_gate"]["checks"]["approval_safety"] = report["approvals"][
                    "release_gate"
                ]["accepted"]
                report["release_gate"]["accepted"] = all(report["release_gate"]["checks"].values())
            if args.baseline:
                report["baseline_comparison"] = compare_reports(
                    report, await asyncio.to_thread(read_report, Path(args.baseline))
                )
            accepted = (
                report["release_gate"]["accepted"]
                and report.get("baseline_comparison", {"accepted": True})["accepted"]
            )
            report["status"] = "PASS" if accepted else "REJECTED"
    except BaseException as exc:
        report["status"] = "FAILED"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        if journal:
            journal.close()
        if gate:
            report["cumulative_usage"] = gate.state
            gate.close()
        if report["status"] == "PASS":
            report.pop("row_journal", None)
        save()
        # The complete report contains every row. Keep the journal only when a
        # failed/interrupted run or failed final save still needs recovery evidence.
        if journal and report["status"] == "PASS":
            await asyncio.to_thread(Path(journal.name).unlink)
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(output),
                "release_gate": report["release_gate"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "PASS" else 1


async def run_approval(args: argparse.Namespace) -> int:
    """The same M17 report schema, with the independent M15 scope selected explicitly."""
    output = local_path(args.output)
    if output.exists():
        raise ConfigurationError("Use a new immutable evaluation report path")
    validate_control_paths(
        [
            Path(args.auth),
            Path(args.pointer),
            Path(args.providers),
            Path(args.approval_budget_state),
            Path(args.rights_key),
        ],
        output=output,
    )
    frozen = approval_cases()
    code = provenance()
    configuration = {
        "approval_dataset": digest(frozen),
        "code": code["package_digest"],
        "providers": file_digest(Path(args.providers)),
        "pointer": file_digest(Path(args.pointer)),
        "scope": "approval",
        "protocol": "m15-durable-approval-v1",
    }
    report: dict[str, Any] = {
        "format": "m17-evaluation-v1",
        "status": "PENDING",
        "live": args.live,
        "stage": args.stage,
        "routes": {},
        "provenance": code,
        "configuration": configuration,
        "experiment_fingerprint": digest(configuration),
        "selected_case_ids": [row["case"] for row in frozen["operations"]],
    }
    try:
        async with asyncio.timeout(args.timeout):
            report["approvals"] = await evaluate_approvals(args, output)
        report["release_gate"] = report["approvals"]["release_gate"]
        if args.baseline:
            report["baseline_comparison"] = compare_reports(
                report, read_report(Path(args.baseline))
            )
        accepted = (
            report["release_gate"]["accepted"]
            and report.get("baseline_comparison", {"accepted": True})["accepted"]
        )
        report["status"] = "PASS" if accepted else "REJECTED"
    except BaseException as exc:
        report["status"] = "FAILED"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        from deephelp_app.dense import atomic_json

        atomic_json(output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(output),
                "metrics": report["approvals"]["metrics"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if accepted else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit", "run", "approval"))
    parser.add_argument("--data", default=str(DATA))
    parser.add_argument("--manifest", default=str(MANIFEST))
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--all-cases",
        action="store_true",
        help="Include non-default live samples; requires --full-evaluation",
    )
    parser.add_argument(
        "--full-evaluation", action="store_true", help="Explicit four-mode quality evaluation"
    )
    parser.add_argument("--mode", action="append", choices=MODES)
    parser.add_argument("--case", action="append", help="Select a whole frozen conversation by ID")
    parser.add_argument(
        "--list-cases", action="store_true", help="Include selectable IDs in offline audit"
    )
    parser.add_argument("--split", choices=("test", "regression"))
    parser.add_argument(
        "--approvals", action="store_true", help="Include the fresh M15 fault/approval scope"
    )
    parser.add_argument(
        "--approval-budget-state", type=local_path, default=Path(".local/m17-approval/budget.json")
    )
    parser.add_argument("--rights-key", type=local_path, default=Path(".local/m15/rights.key"))
    parser.add_argument("--stage", choices=("feature", "main"), default="feature")
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--pointer", default=".local/m09/active.json")
    parser.add_argument("--fasttext-pointer", default=".local/m13/active.json")
    parser.add_argument("--auth", default=".local/m17/auth.json")
    parser.add_argument("--budget-state", default=".local/m17/session-budget.json")
    parser.add_argument("--output", default=".local/m17/offline.json")
    parser.add_argument("--baseline")
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args()
    if args.list_cases and args.command != "audit":
        parser.error("--list-cases requires audit")
    if args.command == "approval" and (
        args.case
        or args.mode
        or args.all_cases
        or args.split
        or args.full_evaluation
        or args.approvals
    ):
        parser.error(
            "approval selects its independent complete scope; case/mode filters apply to run"
        )
    if args.command == "audit":
        cases, summary = audit(Path(args.data), Path(args.manifest))
        if args.list_cases:
            summary["cases"] = [
                {
                    "case_id": c.case_id,
                    "scenario": c.scenario,
                    "split": c.split,
                    "messages": len(c.turns),
                    "live_sample": c.live_sample,
                }
                for c in cases
            ]
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif args.command == "approval":
        raise SystemExit(asyncio.run(run_approval(args)))
    else:
        raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()

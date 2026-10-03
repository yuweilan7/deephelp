"""Three real fallback modes plus optional artifact in the existing HTTP conversation."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx

from deephelp_app.cascade import StructuredFallback
from deephelp_app.conversation import STAGES
from deephelp_app.dense import atomic_json, load_json
from deephelp_app.domain.models import IntentCode, Outcome, ResponseEnvelope
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.evaluation.fasttext_training import audit, metrics
from deephelp_app.evaluation.mvp_acceptance import api_client
from deephelp_app.execution import AsyncCalls
from deephelp_app.fasttext_preprocess import digest
from deephelp_app.fasttext_runtime import (
    LABELS,
    FastTextClassifier,
    FastTextFallback,
    activate,
)
from deephelp_app.learning.event_replay import envelope
from deephelp_app.local_paths import local_path
from deephelp_app.mvp_runtime import (
    BudgetSession,
    LiveAssembly,
    LocalAuth,
    live_app,
    validate_control_paths,
)
from deephelp_app.ports import FallbackPort
from deephelp_app.providers import EndpointConfig, ProviderConfig, create_gateway
from deephelp_app.text_entity import TextEntityProcessor
from deephelp_app.tool_gateway import process_alive


async def compare(
    args: argparse.Namespace, gate: BudgetSession, report: dict[str, Any], save: Any
) -> None:
    experiment = load_json(Path(args.experiment) / "report.json")
    splits = audit()
    models = {
        "raw": FastTextClassifier(Path(str(experiment["selected_raw"]))),
        "quantized": FastTextClassifier(Path(str(experiment["selected_quantized"]))),
    }
    if any(
        m.manifest.train_digest != digest(splits["train"])
        or m.manifest.dev_digest != digest(splits["dev"])
        or m.manifest.test_digest != digest(splits["test"])
        for m in models.values()
    ):
        raise ConfigurationError("Frozen FastText data differs; use a new experiment")
    config = ProviderConfig.load(Path(args.providers))
    endpoint = EndpointConfig.model_validate_json(
        await asyncio.to_thread(
            Path("modules/deephelp-app/event-judge.example.json").read_text, encoding="utf-8"
        )
    )
    async with httpx.AsyncClient(trust_env=False, timeout=60) as client:
        gateway = create_gateway(
            client, config.model_copy(update={"chat": endpoint}), AsyncCalls(1, 45)
        )
        structured = StructuredFallback(gateway)
        ports: dict[str, FallbackPort] = {
            "disabled": structured,
            **{k: FastTextFallback(m, structured) for k, m in models.items()},
        }
        report["classifier"] = {
            k: {
                "bytes": m.manifest.model_bytes,
                "gate": m.manifest.gate.model_dump(),
                "train": metrics(m, splits["train"]),
                "dev": metrics(m, splits["dev"]),
                "test": metrics(m, splits["test"]),
            }
            for k, m in models.items()
        }
        report["fallback_modes"] = {}
        for name, port in ports.items():
            mode: dict[str, Any] = {
                "rows": [],
                "correct": 0,
                "takeovers": 0,
                "wrong_takeovers": 0,
                "model_calls": 0,
            }
            report["fallback_modes"][name] = mode
            durations: list[float] = []
            for row in splits["test"]:
                budget = gate.request()
                text = await TextEntityProcessor().process(
                    envelope(row["text"], "m13-eval"), budget
                )
                started = perf_counter()
                chosen = await port.decide(text, (), budget)
                elapsed = (perf_counter() - started) * 1000
                expected_reason = (
                    "multiple"
                    if row["label"] == "__label__multiple"
                    else ("unknown" if LABELS[row["label"]] is None else "supported")
                )
                correct = chosen.code == LABELS[row["label"]] and chosen.reason == expected_reason
                takeover = chosen.source == "fasttext"
                mode["correct"] += int(correct)
                mode["takeovers"] += int(takeover)
                mode["wrong_takeovers"] += int(takeover and not correct)
                mode["model_calls"] += budget.attempts_used
                durations.append(elapsed)
                mode["rows"].append(
                    {
                        "sample": row,
                        "result": chosen.model_dump(mode="json"),
                        "correct": correct,
                        "elapsed_ms": elapsed,
                        "budget_used": budget.usage().model_dump(mode="json"),
                    }
                )
                save()
            mode.update(
                samples=len(splits["test"]),
                accuracy=mode["correct"] / len(splits["test"]),
                coverage=mode["takeovers"] / len(splits["test"]),
                latency_ms_median=median(durations),
                latency_ms_p95=sorted(durations)[int(len(durations) * 0.95)],
            )
            print(
                json.dumps(
                    {"mode": name, **{k: v for k, v in mode.items() if k != "rows"}},
                    ensure_ascii=False,
                ),
                flush=True,
            )
        report["fallback_diagnostics"] = gateway.diagnostics


async def business(
    args: argparse.Namespace, gate: BudgetSession, report: dict[str, Any], save: Any
) -> None:
    experiment = load_json(Path(args.experiment) / "report.json")
    auth = LocalAuth(Path(args.auth))
    identity = envelope("x", "x").identity
    token = next(t for t, i in auth.rows if i == identity)
    checks = report["checks"]
    report["http_modes"] = {}
    for mode in ("disabled", "raw", "quantized"):
        nonce = uuid4().hex[:16]
        pointer = local_path(str(Path(args.output).with_suffix(f".{mode}.pointer.json")))
        if mode != "disabled":
            activate(
                pointer,
                Path(str(experiment["selected_" + ("raw" if mode == "raw" else "quantized")])),
            )
        collection = "dh_m10_events_m13_probe_" + nonce
        assembly = LiveAssembly(
            Path.cwd(),
            Path(args.providers),
            Path(args.pointer),
            fasttext_pointer=pointer if mode != "disabled" else None,
            event_collection=collection,
        )
        app = live_app(
            assembly, auth, gate, str(Path(args.output).with_suffix(f".{mode}.trace.jsonl"))
        )
        mode_report: dict[str, Any] = {"turns": [], "collection": collection}
        report["http_modes"][mode] = mode_report
        async with app.router.lifespan_context(app):
            assert assembly.ledger and assembly.tools and assembly.event_index
            async with api_client(app, http=True) as client:
                for name, text, code, evidence in (
                    (
                        "discount",
                        "订单000031未享受优惠",
                        IntentCode.DISCOUNT_MISSING,
                        ("99.90", "10.00"),
                    ),
                    (
                        "coupon",
                        "订单000042的券000009不能用",
                        IntentCode.COUPON_UNUSABLE,
                        ("usable", "000009"),
                    ),
                    (
                        "activity",
                        "查询订单000053参加的活动",
                        IntentCode.ORDER_ACTIVITY_QUERY,
                        ("ACTIVITY",),
                    ),
                    (
                        "paraphrase",
                        "账单没扣掉承诺的优惠部分，订单000031",
                        IntentCode.DISCOUNT_MISSING,
                        ("99.90",),
                    ),
                    ("missing", "账单没扣掉承诺的优惠部分", IntentCode.DISCOUNT_MISSING, ()),
                    ("unknown", "我想知道快递走到哪了", None, ()),
                ):
                    body = dict(
                        channel="m13-accept",
                        message_id="m13-" + uuid4().hex,
                        session_id="m13-" + nonce + "-" + name,
                        raw_text=text,
                        occurred_at=datetime.now(UTC).isoformat(),
                    )
                    reply = await client.post(
                        "/converse", json=body, headers={"Authorization": "Bearer " + token}
                    )
                    result = ResponseEnvelope.model_validate(reply.json())
                    key = mode + ":" + name
                    mode_report["turns"].append(
                        dict(name=name, request=body, response=result.model_dump(mode="json"))
                    )
                    checks[key + ":http"] = reply.status_code == 200
                    checks[key + ":13_stages"] = tuple(s.stage for s in result.stages) == STAGES
                    checks[key + ":one_main"] = result.call_counts.intent_calls == 1
                    checks[key + ":intent"] = bool(
                        result.intent_decision and result.intent_decision.final_code == code
                    )
                    if evidence:
                        checks[key + ":facts"] = (
                            result.outcome == Outcome.ANSWERED
                            and all(value in result.reply for value in evidence)
                            and bool(result.evidence_refs)
                        )
                    else:
                        checks[key + ":no_tools"] = not result.tool_call_ids
                        checks[key + ":outcome"] = result.outcome == (
                            Outcome.CLARIFY if name == "missing" else Outcome.HANDOFF
                        )
                    if mode != "disabled":
                        checks[key + ":version"] = result.versions.fasttext_model is not None
                        checks[key + ":enabled"] = "fasttext" not in result.disabled_features
                    if mode != "disabled" and name == "missing":
                        checks[key + ":fasttext_takeover"] = bool(
                            result.intent_decision
                            and result.intent_decision.reason_code == "fasttext_selected"
                        )
                    replay = await client.post(
                        "/converse", json=body, headers={"Authorization": "Bearer " + token}
                    )
                    repeated = ResponseEnvelope.model_validate(replay.json())
                    checks[key + ":replay"] = repeated.run_id == result.run_id and (
                        repeated.call_counts.model_calls == repeated.call_counts.tool_calls == 0
                    )
                    stored = await assembly.ledger.question(
                        identity, body["session_id"], result.question_id or "none"
                    )
                    checks[key + ":mysql"] = bool(
                        stored and stored.status == result.question_status
                    )
                    if name == "missing":
                        supplement = {
                            **body,
                            "message_id": "m13-" + uuid4().hex,
                            "raw_text": "补充：订单000031",
                            "occurred_at": datetime.now(UTC).isoformat(),
                        }
                        followup_reply = await client.post(
                            "/converse",
                            json=supplement,
                            headers={"Authorization": "Bearer " + token},
                        )
                        followup = ResponseEnvelope.model_validate(followup_reply.json())
                        mode_report["turns"].append(
                            dict(
                                name="slot_supplement",
                                request=supplement,
                                response=followup.model_dump(mode="json"),
                            )
                        )
                        checks[key + ":supplement_same_question"] = (
                            followup.question_id == result.question_id
                        )
                        checks[key + ":supplement_facts"] = (
                            followup_reply.status_code == 200
                            and followup.outcome == Outcome.ANSWERED
                            and "99.90" in followup.reply
                            and "10.00" in followup.reply
                            and bool(followup.evidence_refs and followup.tool_call_ids)
                        )
                        checks[key + ":supplement_keeps_intent"] = bool(
                            followup.intent_decision
                            and followup.intent_decision.reason_code == "confirmed_event_context"
                            and followup.intent_decision.final_code == IntentCode.DISCOUNT_MISSING
                        )
                    save()
            mode_report["tool_ledger"] = [
                r.model_dump(mode="json") for r in await assembly.tools.ledger()
            ]
            mode_report["model_diagnostics"] = assembly.gateway.diagnostics
            mode_report["judge_diagnostics"] = assembly.judge_gateway.diagnostics
            index = assembly.event_index
            await index.rpc(
                partial(index.client.drop_collection, collection, timeout=None, retry_times=0)
            )
            checks[mode + ":dedicated_collection_deleted"] = True
        checks[mode + ":mcp_exit"] = assembly.tools.pid is not None and not process_alive(
            assembly.tools.pid
        )
        save()
        print(
            json.dumps(
                {
                    "http_mode": mode,
                    "turns": len(mode_report["turns"]),
                    "failed": [k for k, v in checks.items() if k.startswith(mode + ":") and not v],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


async def run(args: argparse.Namespace) -> int:
    if not args.live:
        raise ConfigurationError("Real M13 fallback/business comparison requires --live")
    output, state = local_path(args.output), local_path(args.budget_state)
    validate_control_paths(
        [state, Path(args.auth), Path(args.pointer), Path(args.providers)], output=output
    )
    if output.exists():
        raise ConfigurationError("Use a new M13 evidence path")
    gate = BudgetSession(state)
    gate.open()
    report: dict[str, Any] = {
        "status": "PENDING",
        "stage": args.stage,
        "synthetic": True,
        "checks": {},
        "quality_claim": "small frozen synthetic group only",
    }

    def save() -> None:
        atomic_json(output, report)

    try:
        async with asyncio.timeout(1200):
            await compare(args, gate, report, save)
            await business(args, gate, report, save)
        report["status"] = "PASS" if all(report["checks"].values()) else "FAIL"
        report["cumulative_budget"] = gate.state
        save()
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "checks": len(report["checks"]),
                    "failed": [k for k, v in report["checks"].items() if not v],
                },
                ensure_ascii=False,
            )
        )
        return 0 if report["status"] == "PASS" else 1
    except Exception as exc:
        report["status"] = "FAIL"
        report["error"] = {"type": type(exc).__name__}
        if isinstance(exc, AppError):
            report["error"].update(code=exc.code, request_id=exc.provider_request_id)
        save()
        raise
    finally:
        gate.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--stage", choices=("feature", "main"), default="feature")
    parser.add_argument("--experiment", default=".local/m13/experiment-v1")
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--pointer", default=".local/m09/active.json")
    parser.add_argument("--auth", default=".local/m13/auth.json")
    parser.add_argument("--budget-state", default=".local/m13/session-budget.json")
    parser.add_argument("--output", required=True)
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()

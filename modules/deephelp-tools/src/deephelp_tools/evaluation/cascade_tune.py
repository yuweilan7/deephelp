"""Dev-only independent score gates; test labels never participate in gate selection."""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx

from deephelp_app.adapters.trace import MemoryTrace
from deephelp_app.application.cascade import ScoreGate, enhanced_query, retrieval_reason
from deephelp_app.application.corpus import digest
from deephelp_app.application.text_entity import TextEntityProcessor
from deephelp_app.bootstrap.local_paths import local_path
from deephelp_app.bootstrap.mvp_runtime import BudgetSession, validate_control_paths
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.models import DenseResult, HybridScope
from deephelp_tools.assets import asset_path
from deephelp_tools.demo.assembly import DemoAssembly as LiveAssembly
from deephelp_tools.evaluation.hybrid_eval import RetrievalQuery
from deephelp_tools.learning.event_replay import envelope


def select_gate(rows: list[dict[str, Any]]) -> tuple[ScoreGate | None, list[dict[str, Any]]]:
    """Maximize correct takeovers with zero dev false takeover; prefer stricter ties."""
    choices = []
    for threshold in (0.55, 0.65, 0.75, 0.85, 0.95):
        for margin in (0.05, 0.1, 0.2, 0.3):
            selected = [
                r
                for r in rows
                if r["eligible"] and r["score"] >= threshold and r["margin"] >= margin
            ]
            wrong = sum(r["gold"] is None or r["top1"] != r["gold"] for r in selected)
            choices.append(
                {
                    "threshold": threshold,
                    "margin": margin,
                    "accepted": len(selected),
                    "wrong": wrong,
                }
            )
    valid = [r for r in choices if r["wrong"] == 0 and r["accepted"] > 0]
    if not valid:
        return None, choices
    best = max(valid, key=lambda r: (r["accepted"], r["threshold"], r["margin"]))
    return ScoreGate(best["threshold"], best["margin"]), choices


async def run(args: argparse.Namespace) -> int:
    if not args.live:
        raise ConfigurationError("Dev score calibration requires explicit --live")
    output, state = local_path(args.output), local_path(args.budget_state)
    policy_path = local_path(args.policy_output)
    validate_control_paths(
        [state, Path(args.pointer), Path(args.providers), policy_path], output=output
    )
    if output.exists() or policy_path.exists():
        raise ValueError("Use new tuning report and policy paths")
    assembly = LiveAssembly(
        Path.cwd(), Path(args.providers), Path(args.pointer), cascade_policy=policy_path
    )
    if not isinstance(assembly.scope, HybridScope):
        raise ConfigurationError("This calibration owns only the frozen M09 Hybrid route")
    dev = [
        RetrievalQuery.model_validate(row)
        for row in json.loads(asset_path("m09_dev.json").read_text(encoding="utf-8"))
    ]
    gate = BudgetSession(state)
    gate.open()
    # Only dev: memory queries are event-linked dev utterances plus a confirmed entity.
    queries: list[dict[str, Any]] = [
        {"id": q.query_id, "text": q.text, "gold": q.gold.value if q.gold else None} for q in dev
    ]
    frozen_digest = digest(
        {"dev": queries, "memory_transform": "cited_event_confirmed_entities_v1"}
    )
    policy: dict[str, Any] = {
        "format": "m12-cascade-v1",
        "dev_digest": frozen_digest,
        "scope_fingerprint": assembly.scope.fingerprint,
        "dense_weight": assembly.pointer.get("dense_weight"),
        "corpus_digest": assembly.pointer["corpus_digest"],
        "gates": {"cosine": None, "fusion": None, "memory_cosine": None, "memory_fusion": None},
    }
    policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
    report: dict[str, Any] = {
        "status": "PENDING",
        "dev_digest": frozen_digest,
        "rows": {},
        "grid": {},
    }
    try:
        async with (
            asyncio.timeout(900),
            httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(retries=0), timeout=60, trust_env=False
            ) as client,
            assembly.open(client, MemoryTrace()) as service,
        ):
            for layer in ("current", "memory"):
                rows = []
                for q in queries:
                    original = await TextEntityProcessor().process(
                        envelope(q["text"], "m12-dev"), gate.request(), fields=()
                    )
                    confirmed = (
                        original.entities
                        or (
                            await TextEntityProcessor().process(
                                envelope("订单000031", "m12-dev"), gate.request(), fields=()
                            )
                        ).entities
                    )
                    query = (
                        q["text"]
                        if layer == "current"
                        else enhanced_query(
                            f"[m12-dev-original] {q['text']}\n[m12-dev-current] 请继续查询",
                            tuple(confirmed),
                        )
                    )
                    result: DenseResult = await service.intent.dense.retrieve(
                        query, assembly.scope, gate.request(), top_k=assembly.top_k
                    )
                    top = result.candidates[0] if result.candidates else None
                    rows.append(
                        {
                            "id": q["id"],
                            "gold": q["gold"],
                            "top1": top.intent_code.value if top else None,
                            "score": top.raw_score if top else 0,
                            "kind": top.score_kind if top else None,
                            "margin": top.raw_score
                            - (result.candidates[1].raw_score if len(result.candidates) > 1 else 0)
                            if top
                            else 0,
                            "eligible": retrieval_reason(result, original, ScoreGate(0, 0))
                            == "calibrated_top1"
                            and len({m.candidate_code for m in original.rule_matches}) <= 1,
                            "retrieval": result.model_dump(mode="json"),
                        }
                    )
                selected, choices = select_gate(rows)
                key = "fusion" if layer == "current" else "memory_fusion"
                policy["gates"][key] = asdict(selected) if selected else None
                report["rows"][layer], report["grid"][layer] = rows, choices
            report.update(status="PASS", policy=policy)
            policy_path.write_text(
                json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "dev_queries_per_layer": len(queries),
                        "gates": policy["gates"],
                    }
                )
            )
    except Exception as exc:
        report.update(status="FAIL", error=type(exc).__name__)
        raise
    finally:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        gate.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="M12 dev-only retrieval score calibration")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--pointer", default=".local/m09/active.json")
    parser.add_argument("--budget-state", required=True)
    parser.add_argument("--policy-output", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()

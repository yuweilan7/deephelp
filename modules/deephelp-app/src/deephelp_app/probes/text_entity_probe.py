"""Fixed synthetic M04 demo and explicit, cumulatively budgeted live content acceptance."""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Any

import httpx

from deephelp_app.domain.models import (
    Entity,
    EntityName,
    EntitySource,
    ErrorCode,
    RequestEnvelope,
    TextEntityResult,
    VerifiedIdentity,
)
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.execution import AsyncCalls, ExecutionBudget
from deephelp_app.local_paths import local_path
from deephelp_app.mvp_runtime import validate_control_paths
from deephelp_app.probes.usage import api_records, summarize_usage
from deephelp_app.providers import ProviderConfig, create_gateway
from deephelp_app.text_entity import TextEntityProcessor
from deephelp_app.trace import MemoryTrace


def load_cases() -> list[dict[str, Any]]:
    corpus = json.loads(
        files("deephelp_app")
        .joinpath("assets/evaluation/text-golden.json")
        .read_text(encoding="utf-8")
    )
    if corpus["synthetic"] is not True or corpus["version"] != "m04-golden-v1":
        raise ConfigurationError("M04 requires the versioned synthetic corpus")
    return list(corpus["cases"])


def sample_request(case: dict[str, Any]) -> RequestEnvelope:
    return RequestEnvelope(
        identity=VerifiedIdentity(tenant_id="synthetic-m04", user_id="u1"),
        channel="synthetic",
        session_id="m04-session",
        message_id=f"m04-{case['id']}",
        raw_text="合成背景说明。" * case.get("prefix_repeat", 0) + case["text"],
        request_id=f"request-{case['id']}",
        trace_id=f"trace-{case['id']}",
        occurred_at=datetime.now(UTC),
        received_at=datetime.now(UTC),
    )


def sample_confirmed(case: dict[str, Any]) -> list[Entity]:
    return [
        Entity(
            name=EntityName(name),
            value=value,
            source=EntitySource(message_id="m04-prior", excerpt=f"合成旧值{value}"),
        )
        for name, value in case.get("confirmed", {}).items()
    ]


def verify_case(case: dict[str, Any], result: TextEntityResult) -> bool:
    return (
        {entity.name.value: entity.value for entity in result.entities} == case["entities"]
        and [name.value for name in result.unresolved_fields] == case["unresolved"]
        and [match.candidate_code.value for match in result.rule_matches] == case["rules"]
        and result.clean.raw_text == sample_request(case).raw_text
        and not result.clean.truncated
    )


async def evaluate(
    processor: TextEntityProcessor,
    budget: ExecutionBudget,
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
    usage_records: list[dict[str, Any]] | None = None,
) -> None:
    for case in cases:
        ports = [port for port in (processor.primary, processor.strong) if port is not None]
        previous = [list(getattr(port, "diagnostics", [])) for port in ports]
        attempts_before = budget.attempts_used
        result = await processor.process(
            sample_request(case), budget, confirmed=sample_confirmed(case)
        )
        observations = [
            row
            for port, before in zip(ports, previous, strict=True)
            for row in api_records(
                getattr(port, "diagnostics", []),
                phase="entity",
                sample=case["id"],
                previous=before,
            )
        ]
        if usage_records is not None:
            usage_records.extend(observations)
        passed = verify_case(case, result)
        results.append(
            {
                "case_id": case["id"],
                "api_usage": summarize_usage(
                    observations,
                    attempts=budget.attempts_used - attempts_before,
                ),
                "status": "PASS" if passed else "FAIL",
                "result": result.model_dump(mode="json"),
            }
        )
        failures = [stat for stat in result.layers if stat.error_code is not None]
        if failures:
            stat = failures[0]
            raise AppError(
                stat.error_code or ErrorCode.MODEL_OUTPUT_INVALID,
                "Live extraction layer failed",
                provider_request_id=stat.provider_request_id,
            )
        if not passed:
            raise AppError(
                ErrorCode.MODEL_OUTPUT_INVALID, "Synthetic M04 content expectation failed"
            )


def layer_summary(results: list[dict[str, Any]]) -> dict[str, object]:
    summary: dict[str, object] = {}
    for name in ("regex", "api", "strong"):
        stats = [
            stat for row in results for stat in row["result"]["layers"] if stat["layer"] == name
        ]
        attempted = sum(any(s["layer"] == name for s in row["result"]["layers"]) for row in results)
        hits = sum(
            any(s["layer"] == name and s["accepted"] > 0 for s in row["result"]["layers"])
            for row in results
        )
        summary[name] = {
            "observations_accepted": sum(s["accepted"] for s in stats),
            "observations_rejected": sum(s["rejected"] for s in stats),
            "model_calls": sum(s["model_calls"] for s in stats),
            "elapsed_ms": round(sum(s["elapsed_ms"] for s in stats), 3),
            "hit_rate": hits / attempted if attempted else None,
            "attempted_case_count": sum(
                any(s["layer"] == name for s in row["result"]["layers"]) for row in results
            ),
            "hit_case_count": sum(
                any(s["layer"] == name and s["accepted"] > 0 for s in row["result"]["layers"])
                for row in results
            ),
        }
    return summary


async def offline_demo() -> int:
    results: list[dict[str, Any]] = []
    cases = [case for case in load_cases() if not case.get("requires_api")]
    budget = ExecutionBudget.start(10, 1, 0)
    await evaluate(TextEntityProcessor(), budget, cases, results)
    print(
        json.dumps(
            {
                "status": "PASS",
                "mode": "offline",
                "cases": len(cases),
                "dataset": "m04-golden-v1",
                "model_calls": budget.attempts_used,
                "layers": layer_summary(results),
                "tail_demo": {
                    "raw_length": len(results[1]["result"]["clean"]["raw_text"]),
                    "segments": len(results[1]["result"]["clean"]["segments"]),
                    "observations": results[1]["result"]["observations"],
                    "unresolved_fields": results[1]["result"]["unresolved_fields"],
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


def selected_cases(args: argparse.Namespace) -> list[dict[str, Any]]:
    cases = load_cases()
    ids = list(getattr(args, "case", None) or [])
    if getattr(args, "all_cases", False):
        if ids:
            raise ConfigurationError("Choose --case or --all-cases, not both")
        return cases
    known = {case["id"] for case in cases}
    if not ids or len(ids) != len(set(ids)) or not set(ids) <= known:
        raise ConfigurationError("Select unique known --case IDs or explicit --all-cases")
    return [case for case in cases if case["id"] in ids]


async def live_probe(args: argparse.Namespace) -> int:
    cases = selected_cases(args)
    if getattr(args, "strong_content", False) and not args.strong_providers:
        raise ConfigurationError("--strong-content requires --strong-providers")
    state_path, output = local_path(args.budget_state), local_path(args.output)
    validate_control_paths(
        [state_path, Path(args.providers)]
        + ([Path(args.strong_providers)] if args.strong_providers else []),
        output=output,
    )
    if output.exists():
        raise ConfigurationError("Use a new immutable entity report path")
    if output in {state_path, state_path.with_suffix(".lock")}:
        raise ConfigurationError("Live output must not overwrite budget state or lock")
    if not state_path.exists():
        raise ConfigurationError("Initialize a cumulative .local M04 budget first")
    lock_path = state_path.with_suffix(".lock")
    try:
        lock = lock_path.open("x", encoding="utf-8")
    except FileExistsError:
        raise ConfigurationError("M04 live budget locked; inspect before recovery") from None
    results: list[dict[str, Any]] = []
    trace = MemoryTrace()
    usage_records: list[dict[str, Any]] = []
    report: dict[str, object] = {
        "status": "PENDING",
        "stage": args.stage,
        "dataset": "m04-golden-v1",
        "results": results,
        "selected_case_ids": [case["id"] for case in cases]
        + (["strong-port-content"] if getattr(args, "strong_content", False) else []),
    }
    try:
        original = json.loads(state_path.read_text(encoding="utf-8-sig"))
        remaining_cost = Decimal(original["max_cost_cny"]) - Decimal(original["cost_upper_cny"])
        if (
            args.max_calls <= 0
            or args.max_tokens <= 0
            or not args.max_cost.is_finite()
            or args.max_cost <= 0
        ):
            raise ConfigurationError("M04 live budgets must be positive and finite")
        budget = ExecutionBudget.start(
            600,
            min(args.max_calls, original["max_calls"] - original["attempts"]),
            0,
            token_limit=min(args.max_tokens, original["max_tokens"] - original["charged_tokens"]),
            cost_limit=min(args.max_cost, remaining_cost),
        )

        def persist() -> None:
            state = dict(original)
            state.update(
                attempts=original["attempts"] + budget.attempts_used,
                tokens=original["tokens"] + budget.tokens_used,
                charged_tokens=original["charged_tokens"] + budget.token_upper_bound,
                cost_upper_cny=str(Decimal(original["cost_upper_cny"]) + budget.cost_upper_bound),
                uncertain_attempts=original.get("uncertain_attempts", 0)
                + budget.uncertain_attempts,
                active_stage=args.stage,
            )
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        budget.on_change = persist
        config = ProviderConfig.load(Path(args.providers))
        strong_config = (
            ProviderConfig.load(Path(args.strong_providers)) if args.strong_providers else None
        )
        async with httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(retries=0),
            timeout=45,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            primary = create_gateway(client, config, AsyncCalls(1, 45))
            strong = (
                create_gateway(client, strong_config, AsyncCalls(1, 45)) if strong_config else None
            )
            report["models"] = {
                "primary": config.chat.model,
                "strong": strong_config.chat.model if strong_config else None,
            }
            processor = TextEntityProcessor(primary, strong, trace=trace)
            try:
                await evaluate(processor, budget, cases, results, usage_records)
                # Direct strong-port acceptance uses a real model; this is separate from escalation.
                if strong is not None and getattr(args, "strong_content", False):
                    strong_case = next(c for c in load_cases() if c["id"] == "quoted-order")
                    await evaluate(
                        TextEntityProcessor(strong=strong, trace=trace),
                        budget,
                        [{**strong_case, "id": "strong-port-content"}],
                        results,
                        usage_records,
                    )
                report["status"] = "PASS"
            except AppError as exc:
                report.update(
                    status="FAIL",
                    error={
                        "code": exc.code,
                        "provider_code": exc.provider_code,
                        "provider_request_id": exc.provider_request_id,
                    },
                )
            finally:
                report.update(
                    budget_used=budget.usage().model_dump(mode="json"),
                    api_usage=summarize_usage(usage_records, attempts=budget.attempts_used),
                    layers=layer_summary(results),
                    trace=[e.model_dump(mode="json") for e in trace.events],
                    diagnostics={
                        "primary": primary.diagnostics,
                        "strong": strong.diagnostics if strong else [],
                    },
                )
                persist()
                current = json.loads(state_path.read_text(encoding="utf-8"))
                current.setdefault("stages", []).append(
                    {
                        "stage": args.stage,
                        "status": report["status"],
                        "output": str(output.relative_to(Path.cwd())),
                    }
                )
                current.pop("active_stage", None)
                state_path.write_text(
                    json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "stage": args.stage,
                    "cases": len(results),
                    "layers": report["layers"],
                    "error": report.get("error"),
                    "budget_used": report["budget_used"],
                },
                ensure_ascii=False,
            )
        )
        return 0 if report["status"] == "PASS" else 1
    finally:
        lock.close()
        lock_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--stage", choices=["feature", "main"], default="feature")
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--strong-providers")
    parser.add_argument(
        "--strong-content", action="store_true", help="Extra direct strong-port content check"
    )
    parser.add_argument(
        "--case", action="append", help="Select a fixed case ID; repeat to add adjacent cases"
    )
    parser.add_argument("--all-cases", action="store_true", help="Explicit full entity corpus")
    parser.add_argument("--list-cases", action="store_true", help="List fixed cases offline")
    parser.add_argument("--budget-state")
    parser.add_argument("--output")
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--max-cost", type=Decimal)
    args = parser.parse_args()
    if args.list_cases:
        print(json.dumps([c["id"] for c in load_cases()], ensure_ascii=False))
        return 0
    if not args.live and (args.case or args.all_cases or args.strong_content):
        parser.error("Case selection is for --live; offline regression uses pytest")
    if args.live and any(
        getattr(args, name) is None
        for name in ("budget_state", "output", "max_calls", "max_tokens", "max_cost")
    ):
        parser.error("--live requires cumulative state/output and explicit call/token/cost limits")
    try:
        return asyncio.run(live_probe(args) if args.live else offline_demo())
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ValueError, OSError:
        print("M04 local configuration failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

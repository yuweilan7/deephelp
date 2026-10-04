"""Frozen evaluation contracts, independent gold checks and denominator-aware metrics."""

import json
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from deephelp_app.adapters.asset_integrity import file_digest as file_digest
from deephelp_app.application.corpus import digest
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.models import DTO, EntityName, IntentCode
from deephelp_app.domain.text_fingerprint import canonical
from deephelp_tools.assets import asset_path

DATA = asset_path("m17_cases.json")
MANIFEST = DATA.with_name("m17_manifest.json")
MODES = ("rule_dense", "hybrid", "memory_event", "fasttext")
LABELS = (*[c.value for c in IntentCode if c.actionable], "unknown", "multiple")


class EvalTurn(DTO):
    turn_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=2000)
    intent: IntentCode | None = None
    label: str
    event_id: str
    entities: dict[str, str] = Field(default_factory=dict)
    expectation: Literal["query", "missing", "ownership", "unknown", "multiple", "followup"]
    basis: str
    business_case: str | None = None
    hint_event: str | None = None
    conflicts: tuple[EntityName, ...] = ()


class EvalCase(DTO):
    case_id: str
    source_group: str
    variant_group: str
    synthetic: Literal[True]
    split: Literal["test", "regression"]
    scenario: str
    live_sample: bool
    turns: tuple[EvalTurn, ...] = Field(min_length=1, max_length=10)


def semantic_label(text: str) -> str:
    """Explicit gold evidence vocabulary, separate from production rule selection."""
    discount = bool(
        re.search(
            r"优惠(?!券).*(?:未|没|消失)|未享受优惠|应减未减|折扣.*(?:没|未)|该少付.*没少", text
        )
    )
    coupon = bool(re.search(r"券.*(?:不能用|无法使用|用不了|不让用|失效|不能抵扣|用不上)", text))
    activity = bool(re.search(r"(?:查|了解|想知道|看看).*(?:活动|促销)|参加.*活动", text))
    codes = [c for ok, c in zip((discount, coupon, activity), LABELS[:3], strict=True) if ok]
    return codes[0] if len(codes) == 1 else "multiple" if codes else "unknown"


def historical_inputs() -> list[str]:
    """Known public index/train/dev/test inputs; excludes M17 itself."""
    values: list[str] = []
    for name in ("m13_fasttext.json", "m09_dev.json", "m09_test.json"):
        values.extend(
            row["text"] for row in json.loads(asset_path(name).read_text(encoding="utf-8"))
        )
    for line in asset_path("m09_corpus.jsonl").read_text(encoding="utf-8").splitlines():
        values.append(json.loads(line)["content"])
    old = json.loads(asset_path("cases.json").read_text(encoding="utf-8"))
    values.extend(row["message"]["raw_text"] for row in old["cases"])
    return values


def expected(turn: EvalTurn) -> dict[str, Any]:
    """Business expectations from versioned synthetic facts, never model answers."""
    if turn.expectation in {"unknown", "multiple"}:
        return {
            "outcome": "HANDOFF" if turn.expectation == "unknown" else "CLARIFY",
            "tools": [],
            "facts": {},
        }
    if turn.expectation == "missing":
        return {"outcome": "CLARIFY", "tools": [], "facts": {}}
    if turn.business_case:
        from deephelp_tools.evaluation.business_catalog import business_case

        case = business_case(turn.business_case)
        return {
            "outcome": {"RESOLVED": "ANSWERED", "HANDED_OFF": "HANDOFF", "FAILED": "ERROR"}[
                case.status.value
            ],
            "tools": [t.value for t in case.tools],
            "facts": {
                k: v.model_dump(mode="json") if hasattr(v, "model_dump") else v
                for k, v in case.expected_facts.items()
            },
            "end_node": case.end_node,
            "error": case.error.value if case.error else None,
        }
    if turn.expectation == "ownership":
        return {"outcome": "ERROR", "tools": ["get_order_benefits"], "facts": {}}
    business = json.loads(asset_path("business.json").read_text(encoding="utf-8"))
    order = next(o for o in business["orders"] if o["order_id"] == turn.entities["order_id"])
    if turn.intent == IntentCode.DISCOUNT_MISSING:
        return {
            "outcome": "ANSWERED" if order["discount_status"] != "missing" else "HANDOFF",
            "tools": ["get_order_benefits"],
            "facts": {
                "order_id": order["order_id"],
                "paid": order["paid"],
                "discount": order["discount"],
                "discount_status": order["discount_status"],
            },
        }
    if turn.intent == IntentCode.COUPON_UNUSABLE:
        coupon = next(
            c for c in business["coupons"] if c["coupon_id"] == turn.entities["coupon_id"]
        )
        return {
            "outcome": "ANSWERED",
            "tools": ["check_coupon"],
            "facts": {
                "order_id": order["order_id"],
                "coupon_id": coupon["coupon_id"],
                "coupon_status": coupon["status"],
                "usable": coupon["status"] == "usable",
            },
        }
    return {
        "outcome": "ANSWERED",
        "tools": ["get_order_benefits"],
        "facts": {"order_id": order["order_id"], "activity_ids": ",".join(order["activity_ids"])},
    }


def audit(data: Path = DATA, manifest: Path = MANIFEST) -> tuple[list[EvalCase], dict[str, Any]]:
    frozen = json.loads(manifest.read_text(encoding="utf-8"))
    raw = json.loads(data.read_text(encoding="utf-8"))
    cases = [EvalCase.model_validate(row) for row in raw]
    expanded = frozen.get("format") == "m17-freeze-v2"
    if (
        frozen.get("format") not in {"m17-freeze-v1", "m17-freeze-v2"}
        or frozen.get("data_sha256") != digest(raw)
        or frozen.get("business_sha256") != file_digest(asset_path("business.json"))
    ):
        raise ConfigurationError("Frozen evaluation data/manifest mismatch")
    business_summary = None
    if expanded:
        from deephelp_tools.evaluation.business_catalog import (
            CATALOG,
            FIXTURES,
            REGISTRY,
            validate_catalog,
        )

        if frozen.get("business_catalog") != {
            str(p.name): file_digest(p) for p in (CATALOG, FIXTURES, REGISTRY)
        }:
            raise ConfigurationError("Frozen business catalog/fixtures/SOP differ")
        business_summary = validate_catalog()
    ids: set[str] = set()
    groups: dict[str, str] = {}
    texts: dict[str, str] = {}
    historical = {canonical(text) for text in historical_inputs()}
    verified = 0
    sequences: set[tuple[str, ...]] = set()
    for case in cases:
        if (
            case.case_id in ids
            or not case.case_id
            or not case.source_group
            or not case.variant_group
        ):
            raise ConfigurationError("Duplicate/empty case identity or group")
        ids.add(case.case_id)
        sequence = tuple(canonical(t.text) for t in case.turns)
        if expanded and sequence in sequences:
            raise ConfigurationError("Identifier-only duplicate conversation cannot increase scale")
        sequences.add(sequence)
        for key in ("source_group", "variant_group"):
            group = key + ":" + getattr(case, key)
            if group in groups and groups[group] != case.split:
                raise ConfigurationError("Source/template group leaks across splits")
            groups[group] = case.split
        prior: dict[str, str] = {}
        for turn in case.turns:
            if turn.turn_id in ids or not turn.basis or turn.label not in LABELS:
                raise ConfigurationError("Duplicate turn or unverified gold evidence")
            ids.add(turn.turn_id)
            if turn.label != (turn.intent.value if turn.intent else turn.label) or (
                (turn.label in LABELS[:3]) != bool(turn.intent and turn.intent.actionable)
            ):
                raise ConfigurationError("Gold label and registered intent differ")
            key = canonical(turn.text)
            if expanded and key in texts and texts[key] != case.split:
                raise ConfigurationError("Normalized message leaks across splits")
            if (
                not expanded
                and key in texts
                and (case.split != "regression" or texts[key] != "regression")
            ):
                raise ConfigurationError("Duplicate normalized evaluation input")
            if case.split == "test" and key in historical:
                raise ConfigurationError("Known historical input cannot be an unseen test")
            texts[key] = case.split
            if turn.expectation == "followup" or turn.hint_event:
                if prior.get(turn.hint_event or turn.event_id) != turn.label:
                    raise ConfigurationError("Followup has no same-event confirmed gold")
            elif semantic_label(turn.text) != turn.label:
                raise ConfigurationError("Gold lacks deterministic semantic evidence")
            prior[turn.event_id] = turn.label
            source = " ".join(
                t.text
                for t in case.turns[: case.turns.index(turn) + 1]
                if t.event_id == turn.event_id or turn.expectation == "multiple"
            )
            if any(
                value not in unicodedata.normalize("NFKC", source)
                for value in turn.entities.values()
            ):
                raise ConfigurationError("Gold entity is absent from conversation evidence")
            if (
                turn.expectation == "missing"
                and turn.intent
                and set(turn.entities) >= {s.value for s in turn.intent.required_slots}
            ):
                raise ConfigurationError("Missing-slot gold already has all required slots")
            expected(turn)
            if turn.business_case:
                from deephelp_tools.evaluation.business_catalog import business_case

                business = business_case(turn.business_case)
                valid_entities = {"order_id": business.order}
                if business.coupon:
                    valid_entities["coupon_id"] = business.coupon
                if turn.intent != business.intent or any(
                    valid_entities.get(k) != v for k, v in turn.entities.items()
                ):
                    raise ConfigurationError("Gold business binding differs from verified fixture")
            verified += 1
    if frozen.get("case_ids") != [c.case_id for c in cases]:
        raise ConfigurationError("Frozen case order differs")
    summary = {
        "data_digest": digest(raw),
        "manifest_digest": digest(frozen),
        "cases": len(cases),
        "turns": verified,
        "independent_cases": len({tuple(canonical(t.text) for t in c.turns) for c in cases}),
        "declared_source_groups": len({c.source_group for c in cases}),
        "variant_groups": len({c.variant_group for c in cases}),
        "synthetic_ratio": 1.0,
        "gold_verification_coverage": 1.0,
        "gold_method": "deterministic semantic evidence + versioned business fixture",
        "split_counts": dict(Counter(c.split for c in cases)),
        "scenarios": len({c.scenario for c in cases}),
        "scenario_scale_complete": bool(business_summary and business_summary["scenarios"] >= 15),
        "scale_500_complete": verified >= 500,
        "independence_limit": (
            "same-author synthetic; canonical/group audit cannot prove semantic independence"
        ),
    }
    if expanded:
        summary["business_catalog"] = business_summary
        summary["unique_normalized_messages"] = len(texts)
        summary["repeated_message_occurrences"] = verified - len(texts)
        summary["scale_500_conversations_complete"] = len(sequences) >= 500
        summary["scale_unit"] = (
            "conversation messages; report independent conversation count separately"
        )
    return cases, summary


def ratio(numerator: int | float, denominator: int, reason: str) -> dict[str, Any]:
    return {
        "value": numerator / denominator if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
        "reason": None if denominator else reason,
    }


def quantile(values: list[float], q: float) -> float | None:
    # Nearest-rank: small sets have a meaningful P95 without out-of-range indexing.
    return sorted(values)[max(0, math.ceil(q * len(values)) - 1)] if values else None


def score(turn: EvalTurn, response: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    gold = expected(turn)
    decision = response.get("intent_decision") or {}
    reason = decision.get("reason_code", "")
    diagnostics = (response.get("event_cluster") or {}).get("diagnostics", [])
    multiple = (
        "multiple" in reason or "multiple_independent_complaints_send_separately" in diagnostics
    )
    label = decision.get("final_code") or ("multiple" if multiple else "unknown")
    actual_entities = observation["entities"]
    facts = {f["name"]: f["value"] for f in response["facts"]}
    tools = observation["tools"]
    params_ok = all(
        all(
            record["parameters"].get(key) == value
            for key, value in turn.entities.items()
            if key in {"order_id", "coupon_id"}
            and (key != "coupon_id" or record["tool_name"] == "check_coupon")
        )
        for record in tools
    )
    facts_ok = all(facts.get(k) == v for k, v in gold["facts"].items())
    hard: list[str] = []
    if response.get("missing_slots") and response["tool_call_ids"]:
        hard.append("missing_slots_called_tool")
    if turn.expectation in {"missing", "unknown", "multiple"} and response["tool_call_ids"]:
        hard.append("prohibited_business_dispatch")
    if turn.expectation == "ownership" and (response["facts"] or response["outcome"] == "ANSWERED"):
        hard.append("ownership_fact_leak")
    if response["outcome"] == "ANSWERED" and (
        not response["facts"] or not response["evidence_refs"] or not facts_ok
    ):
        hard.append("unsupported_answer")
    if tools and not params_ok:
        hard.append("wrong_tool_object")
    if set(response["tool_call_ids"]) != {t["call_id"] for t in tools}:
        hard.append("tool_ledger_mismatch")
    succeeded = {e for t in tools if t["status"] == "succeeded" for e in t["evidence_ids"]}
    if any(
        not f["evidence_ids"] or not set(f["evidence_ids"]) <= succeeded for f in response["facts"]
    ):
        hard.append("facts_without_successful_tool_evidence")
    if response.get("call_counts", {}).get("intent_calls", 0) > 1:
        hard.append("multiple_main_classifier_calls")
    tool_names = [record["tool_name"] for record in tools]
    checks = {
        "intent": label == turn.label,
        "outcome": response["outcome"] == gold["outcome"],
        "entities": actual_entities == turn.entities
        if turn.expectation != "multiple" and not turn.conflicts
        else None,
        "tool_sequence": tool_names == gold["tools"],
        "tool_parameters": params_ok,
        "facts": facts_ok,
    }
    if gold.get("end_node"):
        checks["sop_terminal"] = bool(
            response.get("sop_node_path") and response["sop_node_path"][-1] == gold["end_node"]
        )
    if gold.get("error"):
        checks["error"] = (response.get("error") or {}).get("code") == gold["error"]
    if turn.conflicts:
        actual_unresolved = set(observation.get("unresolved_fields", []))
        checks["unresolved_fields"] = {f.value for f in turn.conflicts} <= actual_unresolved and (
            not turn.intent or actual_unresolved <= {f.value for f in turn.intent.required_slots}
        )
    sources = observation.get("entity_sources")
    if sources is not None:
        checks["entity_provenance"] = (
            all(
                sources.get(key, {}).get("message_id")
                in observation["allowed_sources"].get(key, [])
                and value in unicodedata.normalize("NFKC", sources.get(key, {}).get("excerpt", ""))
                for key, value in turn.entities.items()
            )
            if turn.expectation != "multiple" and turn.entities
            else None
        )
    return {
        "gold_label": turn.label,
        "predicted_label": label,
        "checks": checks,
        "completed": all(value for value in checks.values() if value is not None) and not hard,
        "hard_failures": hard,
        "gold": gold,
        "entity_counts": {
            "gold": len(turn.entities),
            "predicted": len(actual_entities),
            "correct": sum(
                actual_entities.get(key) == value for key, value in turn.entities.items()
            ),
        }
        if turn.expectation != "multiple"
        else None,
    }


def metrics(rows: list[dict[str, Any]], *, live: bool, event_enabled: bool) -> dict[str, Any]:
    matrix = {label: {pred: 0 for pred in LABELS} for label in LABELS}
    for row in rows:
        matrix[row["score"]["gold_label"]][row["score"]["predicted_label"]] += 1
    f1: dict[str, Any] = {}
    for label in LABELS:
        tp = matrix[label][label]
        fp = sum(matrix[g][label] for g in LABELS if g != label)
        fn = sum(matrix[label][p] for p in LABELS if p != label)
        f1[label] = ratio(2 * tp, 2 * tp + fp + fn, "class absent from gold and predictions")
    present = [v["value"] for v in f1.values() if v["value"] is not None]
    retrieval = [
        (row, r)
        for row in rows
        for r in row["observation"]["retrievals"]
        if row["score"]["gold_label"] in LABELS[:3]
    ]
    tools = [t for row in rows for t in row["observation"]["tools"]]
    takeovers = [
        row
        for row in rows
        if any(
            step["layer"] in {"current", "memory", "fallback"}
            and (
                step["reason"] == "calibrated_top1"
                or (step.get("fasttext") or {}).get("is_actionable")
            )
            for step in (row["response"].get("intent_decision") or {}).get("cascade_steps", [])
        )
    ]
    pairs = (
        [(a, b) for i, a in enumerate(rows) for b in rows[i + 1 :] if a["case_id"] == b["case_id"]]
        if event_enabled
        else []
    )
    different = [(a, b) for a, b in pairs if a["event_id"] != b["event_id"]]
    same = [(a, b) for a, b in pairs if a["event_id"] == b["event_id"]]
    budget = [row["response"]["budget_used"] for row in rows]
    by_case: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], []).append(row)
    case_scores = {
        case: all(r["score"]["completed"] for r in group)
        and all(
            (a["event_id"] == b["event_id"])
            == (a["response"]["question_id"] == b["response"]["question_id"])
            for i, a in enumerate(group)
            for b in group[i + 1 :]
        )
        for case, group in by_case.items()
    }
    supported = [r for r in rows if r["score"]["gold_label"] in LABELS[:3]]
    provenance = [
        r["score"]["checks"]["entity_provenance"]
        for r in rows
        if r["score"]["checks"].get("entity_provenance") is not None
    ]
    gold_tools = [r for r in rows if r["score"]["gold"]["tools"]]
    entity_counts = [r["score"]["entity_counts"] for r in rows if r["score"].get("entity_counts")]
    return {
        "turns": len(rows),
        "confusion_matrix": matrix,
        "per_class_f1": f1,
        "classification_counts": dict(Counter(r["score"]["predicted_label"] for r in rows)),
        "accuracy": ratio(sum(r["score"]["checks"]["intent"] for r in rows), len(rows), "not run"),
        "macro_f1": sum(present) / len(present) if present else None,
        "supported_intent_accuracy": ratio(
            sum(r["score"]["checks"]["intent"] for r in supported),
            len(supported),
            "no supported-intent gold",
        ),
        "supported_macro_f1": (
            sum(f1[label]["value"] for label in LABELS[:3] if f1[label]["value"] is not None)
            / sum(f1[label]["value"] is not None for label in LABELS[:3])
            if any(f1[label]["value"] is not None for label in LABELS[:3])
            else None
        ),
        "recall_at_k": {
            str(k): ratio(
                sum(
                    row["score"]["gold_label"] in [h["intent_code"] for h in r["hits"][:k]]
                    for row, r in retrieval
                ),
                len(retrieval),
                "no eligible retrieval queries",
            )
            for k in (1, 3)
        },
        "entity_exact": ratio(
            sum(
                r["score"]["checks"]["entities"]
                for r in rows
                if r["score"]["checks"]["entities"] is not None
            ),
            sum(r["score"]["checks"]["entities"] is not None for r in rows),
            "not run / ambiguous multi-event entities",
        ),
        "entity_provenance": ratio(
            sum(provenance), len(provenance), "not observed / ambiguous entities"
        ),
        "entity_precision": ratio(
            sum(c["correct"] for c in entity_counts),
            sum(c["predicted"] for c in entity_counts),
            "no predicted entities",
        ),
        "entity_recall": ratio(
            sum(c["correct"] for c in entity_counts),
            sum(c["gold"] for c in entity_counts),
            "no gold entities",
        ),
        "event_wrong_merge": ratio(
            sum(a["response"]["question_id"] == b["response"]["question_id"] for a, b in different),
            len(different),
            "no distinct-event pairs / capability disabled",
        ),
        "event_wrong_split": ratio(
            sum(a["response"]["question_id"] != b["response"]["question_id"] for a, b in same),
            len(same),
            "no same-event pairs / capability disabled",
        ),
        "clarifications": sum(r["response"]["outcome"] == "CLARIFY" for r in rows),
        "takeover_rate": ratio(len(takeovers), len(rows), "not run"),
        "wrong_takeover_rate": ratio(
            sum(not r["score"]["checks"]["intent"] for r in takeovers),
            len(takeovers),
            "no takeovers",
        ),
        "tool_calls": len(tools),
        "tool_parameter_accuracy": ratio(
            sum(r["score"]["checks"]["tool_parameters"] for r in rows if r["observation"]["tools"]),
            sum(bool(r["observation"]["tools"]) for r in rows),
            "no tool dispatches",
        ),
        "completion_rate": ratio(sum(r["score"]["completed"] for r in rows), len(rows), "not run"),
        "case_completion_rate": ratio(
            sum(case_scores.values()), len(case_scores), "no complete cases"
        ),
        "case_failures": [case for case, ok in case_scores.items() if not ok],
        "gold_tool_behavior_accuracy": ratio(
            sum(
                r["score"]["checks"]["tool_sequence"] and r["score"]["checks"]["tool_parameters"]
                for r in gold_tools
            ),
            len(gold_tools),
            "no gold-permitted tool scenarios",
        ),
        "latency_p50_ms": quantile([r["elapsed_ms"] for r in rows], 0.5),
        "latency_p95_ms": quantile([r["elapsed_ms"] for r in rows], 0.95),
        "calls": {
            key: sum(r["response"]["call_counts"][key] for r in rows)
            for key in ("intent_calls", "model_calls", "retrieval_calls", "tool_calls")
        },
        "real_model_attempts": sum(r["response"]["call_counts"]["model_calls"] for r in rows)
        if live and rows
        else None,
        "shared_budget_attempts": sum(b["attempts"] for b in budget) if rows else None,
        "fasttext_predictions": sum(
            bool(s.get("fasttext"))
            for r in rows
            for s in (r["response"].get("intent_decision") or {}).get("cascade_steps", [])
        )
        if live
        else None,
        "fasttext_takeovers": sum(
            (r["response"].get("intent_decision") or {}).get("reason_code") == "fasttext_selected"
            for r in rows
        )
        if live
        else None,
        "real_tokens": sum(b["tokens"] or 0 for b in budget) if live and rows else None,
        "estimated_cost_cny": sum(float(b["cost"] or 0) for b in budget) if live and rows else None,
        "cost_note": "configured estimate, not settled billing"
        if live
        else "offline adapters: real usage not run",
        "failures": [
            {"case_id": r["case_id"], "turn_id": r["turn_id"], **r["score"]}
            for r in rows
            if not r["score"]["completed"]
        ],
    }


def capability_eligible(route: dict[str, Any], row: dict[str, Any]) -> bool:
    # Preserve all raw mistakes. Explicit hints are supported even by stateless
    # ablations; only automatic attachment and segmentation require events.
    return route.get("capabilities", {}).get("events", True) or not (
        row.get("expectation") == "followup"
        and not row.get("hint_event")
        or row.get("expectation") == "multiple"
    )


def targeted_gate(routes: dict[str, dict[str, Any]], *, complete: bool) -> dict[str, Any]:
    """Keep content/safety gates for selected routes without inventing ablation evidence."""
    checks = {
        "all_requested_rows_completed": complete and bool(routes),
        "selected_content": all(
            row["score"]["completed"]
            for route in routes.values()
            for row in route["rows"]
            if capability_eligible(route, row)
        ),
        "hard_cases": all(
            not row["score"]["hard_failures"] for route in routes.values() for row in route["rows"]
        ),
        "no_wrong_event_merge": all(
            not route["metrics"]["event_wrong_merge"]["numerator"]
            for route in routes.values()
            if route.get("capabilities", {}).get("events", True)
        ),
        "persistence_and_replay": all(all(route["checks"].values()) for route in routes.values()),
    }
    return {
        "accepted": all(checks.values()),
        "checks": checks,
        "scope": "selected fixed cases and modes only; not a full quality/release comparison",
        "ablation_comparison": None,
    }


def release_gate(routes: dict[str, dict[str, Any]], *, complete: bool) -> dict[str, Any]:
    baseline = routes["rule_dense"]["rows"]
    candidate = routes["fasttext"]["rows"]
    # Paired single-turn comparison; disabled multi-turn capabilities are reported separately.
    comparable = [r["turn_id"] for r in baseline if not r["multi_turn"]]
    base = sum(r["score"]["completed"] for r in baseline if r["turn_id"] in comparable)
    final = sum(r["score"]["completed"] for r in candidate if r["turn_id"] in comparable)
    hard = [
        {"mode": mode, "turn_id": row["turn_id"], "failures": row["score"]["hard_failures"]}
        for mode, route in routes.items()
        for row in route["rows"]
        if row["score"]["hard_failures"]
    ]
    events = routes["fasttext"]["metrics"]["event_wrong_merge"]

    regression_scope = {
        mode: {
            "checked_turns": sum(
                capability_eligible(route, r)
                for r in route["rows"]
                if r.get("split") == "regression"
            ),
            "diagnostic_turn_ids": [
                r["turn_id"]
                for r in route["rows"]
                if r.get("split") == "regression" and not capability_eligible(route, r)
            ],
        }
        for mode, route in routes.items()
    }
    checks = {
        "all_requested_rows_completed": complete,
        "hard_cases": not hard,
        "paired_single_turn_no_regression": final >= base,
        "confirmed_regressions": all(
            r["score"]["completed"]
            for mode, route in routes.items()
            for r in route["rows"]
            if r.get("split") == "regression" and capability_eligible(route, r)
        ),
        "fasttext_no_regression_from_memory_event": (
            sum(r["score"]["completed"] for r in candidate)
            >= sum(r["score"]["completed"] for r in routes["memory_event"]["rows"])
        ),
        "no_wrong_event_merge": not events["numerator"],
        "persistence_and_replay": all(all(route["checks"].values()) for route in routes.values()),
    }
    return {
        "accepted": all(checks.values()),
        "checks": checks,
        "hard_failures": hard,
        "paired_cases": len(comparable),
        "baseline_completed": base,
        "candidate_completed": final,
        "regression_scope": regression_scope,
        "scope": "this fixed synthetic subset; not 500+ or enterprise quality",
    }

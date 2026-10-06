"""在600内部逐层收口意图：规则、当前召回、必要记忆召回、最终兜底。

所有层共享原预算，命中可靠结论立即停止；不会每层都调用模型。
不同来源的分数只使用对应校准阈值和间隔，重排顺序不能借用旧融合阈值。
finish组合选择与槽位状态，retrieval_reason解释为何继续；网络/预算错误仍向上报告。
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from pydantic import ValidationError

from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import ErrorCode
from deephelp_app.domain.models.entities import Entity
from deephelp_app.domain.models.evidence import EvidenceRef, EvidenceSource
from deephelp_app.domain.models.intent_decisions import (
    CascadeStep,
    Decision,
    FallbackResult,
    IntentDecision,
)
from deephelp_app.domain.models.intents import IntentCode
from deephelp_app.domain.models.model_io import ChatMessage, ChatRequest
from deephelp_app.domain.models.retrieval import DenseResult, DenseScope, IntentCandidate, ScoreKind
from deephelp_app.domain.models.text import DemandType, TextEntityResult
from deephelp_app.domain.ports import ChatPort, DenseRetrieverPort, FallbackPort
from deephelp_app.domain.registry import complaint_registry


@dataclass(frozen=True)
class ScoreGate:
    threshold: float
    margin: float

    def __post_init__(self) -> None:
        if not 0 <= self.threshold <= 2 or not 0 <= self.margin <= 2:
            raise ValueError("Invalid source-specific retrieval gate")


@dataclass(frozen=True)
class CascadePolicy:
    # None deliberately disables raw-score takeover until dev calibration exists.
    cosine: ScoreGate | None = None
    fusion: ScoreGate | None = None
    memory_cosine: ScoreGate | None = None
    memory_fusion: ScoreGate | None = None
    source: str = "uncalibrated_no_score_takeover"
    max_query_chars: int = 2000
    scope_fingerprint: str | None = None
    dense_weight: float | None = None
    corpus_digest: str | None = None

    @classmethod
    def load(cls, path: Path) -> CascadePolicy:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["format"] != "m12-cascade-v1" or not data["dev_digest"]:
                raise ValueError("Unversioned cascade policy")
            gates = data["gates"]

            def score(name: str) -> ScoreGate | None:
                return ScoreGate(**gates[name]) if gates.get(name) else None

            return cls(
                cosine=score("cosine"),
                fusion=score("fusion"),
                memory_cosine=score("memory_cosine"),
                memory_fusion=score("memory_fusion"),
                source=data["dev_digest"],
                scope_fingerprint=data["scope_fingerprint"],
                dense_weight=data["dense_weight"],
                corpus_digest=data["corpus_digest"],
            )
        except OSError, KeyError, TypeError, ValueError:
            raise ConfigurationError("Invalid calibrated cascade policy") from None

    def gate(self, kind: str, memory: bool) -> ScoreGate | None:
        return getattr(self, ("memory_" if memory else "") + kind, None)


class StructuredFallback:
    """One task-sized schema call; optional FastText wraps this same port."""

    def __init__(
        self,
        model: ChatPort,
        *,
        system_prompt: str | None = None,
        catalog_version: str = "complaints-v1",
    ) -> None:
        from deephelp_app.application.intent_prompt import default_system

        self.model = model
        self.catalog_version = catalog_version
        self.system_prompt = (
            system_prompt if system_prompt is not None else default_system(catalog_version)
        )

    async def decide(
        self,
        text: TextEntityResult,
        retrievals: tuple[DenseResult, ...],
        budget: ExecutionBudget,
        *,
        context: str | None = None,
    ) -> FallbackResult:
        from deephelp_app.application.intent import intent_schema

        result = await self.model.chat(
            ChatRequest(
                messages=[
                    ChatMessage(
                        role="system",
                        content=self.system_prompt,
                    ),
                    ChatMessage(
                        role="user",
                        content=json.dumps(
                            {
                                "text": text.clean.cleaned_text,
                                "confirmed_event_context": context,
                                "retrievals": [
                                    {
                                        "candidates": r.model_dump(mode="json")["candidates"],
                                        "examples": [h.content for h in r.hits],
                                    }
                                    for r in retrievals
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    ),
                ],
                response_format="json_schema",
                output_schema=intent_schema(self.catalog_version),
                max_output_tokens=2048,
                repair_once=False,
            ),
            budget,
        )
        try:
            return FallbackResult.model_validate(result.structured)
        except ValidationError:
            raise AppError(
                ErrorCode.MODEL_OUTPUT_INVALID, "Invalid fallback registry choice"
            ) from None


def enhanced_query(summary: str, entities: tuple[Entity, ...]) -> str:
    slots = ", ".join(f"{e.name.value}={e.value}" for e in entities)
    return summary + ("\nCONFIRMED ENTITIES: " + slots if slots else "")


def require_attempt(budget: ExecutionBudget) -> None:
    budget.remaining_seconds()
    if budget.remaining_attempts <= 0:
        raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Cascade call budget exhausted")


def retrieval_reason(result: DenseResult, text: TextEntityResult, gate: ScoreGate | None) -> str:
    """解释当前召回能否按该来源的校准门槛直接选择。

    同时检查候选证据、实体约束、阈值与领先间隔；重排后的顺序须交最终兜底。
    返回理由字符串供Cascade记录下一步，它不是新的模型判断。
    """
    if result.rerank is not None and result.rerank.status == "passed":
        return "rerank_requires_semantic_confirmation"
    if not result.candidates:
        return "no_candidates"
    if any(not c.intent_code.actionable for c in result.candidates):
        return "non_actionable_candidate"
    if len({c.intent_code for c in result.candidates}) != len(result.candidates) or [
        c.rank for c in result.candidates
    ] != list(range(1, len(result.candidates) + 1)):
        return "invalid_candidate_ranks"
    top = result.candidates[0]
    hit = next((h for h in result.hits if h.doc_id == top.evidence_doc_id), None)
    if (
        hit is None
        or hit.intent_code != top.intent_code
        or hit.score_kind != top.score_kind
        or hit.raw_score != top.raw_score
    ):
        return "candidate_evidence_mismatch"
    # Explicit index constraints differ from incidental synthetic IDs in example prose.
    constraints = hit.metadata.get("entity_constraints", {})
    values = {e.name.value: e.value for e in text.entities}
    if not isinstance(constraints, dict) or any(
        key in values and values[key] != value for key, value in constraints.items()
    ):
        return "entity_counterevidence"
    if text.unresolved_fields:
        return "unresolved_entities"
    if gate is None:
        return "score_takeover_disabled"
    if any(c.score_kind != top.score_kind for c in result.candidates):
        return "incomparable_scores"
    second = result.candidates[1].raw_score if len(result.candidates) > 1 else 0.0
    if top.raw_score < gate.threshold:
        return "below_threshold"
    if top.raw_score - second < gate.margin:
        return "close_candidates"
    return "calibrated_top1"


class Cascade:
    def __init__(
        self,
        retrieval: DenseRetrieverPort,
        scope: DenseScope,
        fallback: FallbackPort,
        policy: CascadePolicy,
        top_k: int,
    ) -> None:
        self.retrieval, self.scope, self.fallback = retrieval, scope, fallback
        self.policy, self.top_k = policy, top_k
        if policy.scope_fingerprint and policy.scope_fingerprint != scope.fingerprint:
            raise ConfigurationError("Cascade calibration scope differs; recalibrate")

    async def recognize(
        self,
        text: TextEntityResult,
        budget: ExecutionBudget,
        *,
        context_intent: IntentCode | None = None,
        memory_query: str | None = None,
    ) -> IntentDecision:
        steps: list[CascadeStep] = []
        results: list[DenseResult] = []
        available = tuple(e.name for e in text.entities if e.name not in text.unresolved_fields)

        def finish(
            code: IntentCode | None,
            reason: str,
            *,
            decision: Decision | None = None,
            kind: ScoreKind = ScoreKind.RULE,
            evidence: tuple[EvidenceRef, ...] = (),
        ) -> IntentDecision:
            if (
                code is not None
                and code not in complaint_registry(self.scope.registry_version).actionable_codes
            ):
                raise AppError(ErrorCode.VERSION_CONFLICT, "Intent exceeds pinned catalog")
            action = decision or (
                Decision.ACCEPT
                if code and set(code.required_slots) <= set(available)
                else Decision.CLARIFY
                if code
                else Decision.HANDOFF
            )
            return IntentDecision(
                registry_version=self.scope.registry_version,
                decision=action,
                final_code=code,
                reason_code=reason,
                available_slots=available,
                candidates=(IntentCandidate(code=code, rank=1, score=1, score_kind=kind),)
                if code
                else (),
                stage_evidence=evidence,
                retrieval=results[-1] if results else None,
                cascade_steps=tuple(steps),
                policy_version="cascade-policy-v1",
            )

        budget.remaining_seconds()
        if re.search(
            r"我没有.{0,16}问题|不用查|不想查询|不需要查询|不是来投诉|不需要你调用",
            text.clean.cleaned_text,
        ):
            steps.append(CascadeStep(layer="rule", action="handoff", reason="user_declined_query"))
            return finish(None, "user_declined_query")
        codes = list(dict.fromkeys(m.candidate_code for m in text.rule_matches))
        if context_intent is not None:
            if (codes and codes != [context_intent]) or text.demand_type == DemandType.NEW_TOPIC:
                raise AppError(
                    ErrorCode.INVALID_ARGUMENT, "Current topic differs from pinned event"
                )
            action: Literal["accept", "clarify"] = (
                "accept" if set(context_intent.required_slots) <= set(available) else "clarify"
            )
            steps.append(
                CascadeStep(layer="context", action=action, reason="confirmed_event_context")
            )
            return finish(
                context_intent,
                "confirmed_event_context",
                evidence=(
                    EvidenceRef(
                        evidence_id="confirmed-event-context",
                        source=EvidenceSource.MESSAGE,
                        record_id=text.message_id,
                        summary="Authorized event retains its pinned intent",
                    ),
                ),
            )
        if len(codes) > 1:
            steps.append(CascadeStep(layer="rule", action="clarify", reason="multiple_intents"))
            return finish(None, "multiple_intents", decision=Decision.CLARIFY)
        if codes:
            match = text.rule_matches[0]
            action = "accept" if set(codes[0].required_slots) <= set(available) else "clarify"
            steps.append(CascadeStep(layer="rule", action=action, reason="rule_selected"))
            return finish(
                codes[0],
                "rule_selected",
                evidence=(
                    EvidenceRef(
                        evidence_id="rule-" + match.rule_id,
                        source=EvidenceSource.MESSAGE,
                        record_id=text.message_id,
                        version=match.rule_version,
                        summary=match.evidence.excerpt,
                    ),
                ),
            )
        steps.append(CascadeStep(layer="rule", action="continue", reason="no_unique_rule"))
        if len(text.clean.cleaned_text) > self.policy.max_query_chars:
            return finish(None, "long_unmatched_input")
        queries: list[tuple[Literal["current", "memory"], str]] = [
            ("current", text.clean.cleaned_text)
        ]
        if memory_query and memory_query != text.clean.cleaned_text:
            queries.append(("memory", memory_query))
        for layer, query in queries:
            if len(query) > self.policy.max_query_chars:
                steps.append(
                    CascadeStep(layer=layer, action="handoff", reason="query_context_too_long")
                )
                return finish(None, "query_context_too_long")
            require_attempt(budget)
            result = await self.retrieval.retrieve(query, self.scope, budget, top_k=self.top_k)
            if result.scope != self.scope or any(h.scope != self.scope for h in result.hits):
                raise AppError(ErrorCode.VERSION_CONFLICT, "Retrieval scope changed")
            results.append(result)
            kind = result.candidates[0].score_kind if result.candidates else "fusion"
            reason = retrieval_reason(result, text, self.policy.gate(kind, layer == "memory"))
            accepted = reason == "calibrated_top1"
            steps.append(
                CascadeStep(
                    layer=layer,
                    action=(
                        "accept"
                        if accepted
                        and set(result.candidates[0].intent_code.required_slots) <= set(available)
                        else "clarify"
                        if accepted
                        else "continue"
                    ),
                    reason=reason,
                    retrieval=result,
                    retrieval_calls=1,
                )
            )
            if accepted:
                candidate = result.candidates[0]
                evidence = tuple(
                    EvidenceRef(
                        evidence_id=f"{layer}-{h.doc_id}",
                        source=EvidenceSource.DATASET,
                        record_id=h.doc_id,
                        version=self.scope.dataset_version,
                        summary=h.content,
                    )
                    for h in result.hits
                )
                decision = finish(
                    candidate.intent_code, f"{layer}_retrieval_selected", evidence=evidence
                )
                # Preserve actual ranked raw candidates, rather than inventing score=1.
                return IntentDecision.model_validate(
                    decision.model_copy(
                        update={
                            "candidates": tuple(
                                IntentCandidate(
                                    code=c.intent_code,
                                    rank=c.rank,
                                    score=c.raw_score,
                                    score_kind=ScoreKind(c.score_kind),
                                )
                                for c in result.candidates
                            )
                        }
                    ).model_dump()
                )
        require_attempt(budget)
        before = budget.attempts_used
        try:
            chosen = await self.fallback.decide(text, tuple(results), budget, context=memory_query)
            # Validate custom Ports as well as the structured provider adapter.
            chosen = FallbackResult.model_validate(chosen.model_dump())
        except (ValidationError, AppError) as exc:
            if isinstance(exc, AppError) and exc.code != ErrorCode.MODEL_OUTPUT_INVALID:
                raise
            steps.append(
                CascadeStep(
                    layer="fallback",
                    action="handoff",
                    reason="invalid_registry_choice",
                    model_calls=budget.attempts_used - before,
                )
            )
            return finish(None, "invalid_registry_choice")
        fallback_action: Literal["accept", "clarify", "handoff"] = (
            "clarify"
            if chosen.reason == "multiple"
            else "handoff"
            if not chosen.code
            else "accept"
            if set(chosen.code.required_slots) <= set(available)
            else "clarify"
        )
        steps.append(
            CascadeStep(
                layer="fallback",
                action=fallback_action,
                reason=chosen.reason,
                model_calls=budget.attempts_used - before,
                fasttext=chosen.fasttext,
            )
        )
        decision = finish(
            chosen.code,
            "fasttext_selected" if chosen.source == "fasttext" else "fallback_" + chosen.reason,
            decision=Decision.CLARIFY if chosen.reason == "multiple" else None,
            kind=ScoreKind.CLASSIFIER_PROBABILITY
            if chosen.source == "fasttext"
            else ScoreKind.MODEL_CHOICE,
        )
        if chosen.source == "fasttext" and chosen.fasttext:
            decision = IntentDecision.model_validate(
                decision.model_copy(
                    update={
                        "candidates": tuple(
                            IntentCandidate(
                                code=cast(IntentCode, c.code),
                                rank=i,
                                score=min(1.0, c.raw_probability),
                                score_kind=ScoreKind.CLASSIFIER_PROBABILITY,
                            )
                            for i, c in enumerate(
                                (c for c in chosen.fasttext.top_k if c.code is not None), 1
                            )
                        )
                    }
                ).model_dump()
            )
        return decision

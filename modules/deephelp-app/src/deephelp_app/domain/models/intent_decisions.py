"""Canonical intent decisions values and validation; no runtime resources."""

from __future__ import annotations

from enum import StrEnum
from typing import Final, Literal

from pydantic import (
    Field,
    model_validator,
)

from .common import DTO, POLICY_VERSION, REGISTRY_VERSION, Count, Identifier, PositiveCount
from .entities import EntityName
from .evidence import EvidenceRef, EvidenceSource
from .intents import IntentCode
from .retrieval import DenseResult, IntentCandidate


class Decision(StrEnum):
    ACCEPT = "accept"
    CLARIFY = "clarify"
    HANDOFF = "handoff"
    REJECT = "reject"


class IntentOverride(DTO):
    override_rule_id: Literal["prefer_complete_slots_v1"]
    reason: Literal["top1_missing_slots_selected_complete"]
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)


FASTTEXT_LABELS: Final[dict[str, IntentCode | None]] = {
    "__label__discount": IntentCode.DISCOUNT_MISSING,
    "__label__coupon": IntentCode.COUPON_UNUSABLE,
    "__label__activity": IntentCode.ORDER_ACTIVITY_QUERY,
    "__label__unknown": None,
    "__label__multiple": None,
}


class FastTextCandidate(DTO):
    label: Identifier
    code: IntentCode | None
    rank: PositiveCount
    # fastText exposes softmax + 1e-5; retain that raw score without renormalizing it.
    raw_probability: float = Field(ge=0, le=1.0001, allow_inf_nan=False)

    @model_validator(mode="after")
    def registered_label(self) -> FastTextCandidate:
        if self.label not in FASTTEXT_LABELS or self.code != FASTTEXT_LABELS[self.label]:
            raise ValueError("FastText label and registry code disagree")
        return self


class FastTextPrediction(DTO):
    top_k: tuple[FastTextCandidate, ...] = Field(default=(), max_length=5)
    final_intent_code: IntentCode | None = None
    confidence_kind: Literal["classifier_probability"] = "classifier_probability"
    is_actionable: bool = False
    need_review: bool = True
    unknown: bool = True
    reason: Identifier
    model_version: Identifier
    preprocessing_signature: Identifier
    policy_version: Identifier
    quantized: bool
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def consistent_selection(self) -> FastTextPrediction:
        if [c.rank for c in self.top_k] != list(range(1, len(self.top_k) + 1)):
            raise ValueError("FastText ranks must start at one")
        if len({c.label for c in self.top_k}) != len(self.top_k):
            raise ValueError("Duplicate FastText labels")
        if any(c.code is not None and not c.code.actionable for c in self.top_k):
            raise ValueError("FastText codes must be registry leaves")
        if any(
            a.raw_probability < b.raw_probability
            for a, b in zip(self.top_k, self.top_k[1:], strict=False)
        ):
            raise ValueError("FastText candidates must be sorted by raw probability")
        selected = self.final_intent_code is not None
        if self.is_actionable != selected or self.need_review == selected:
            raise ValueError("FastText selection and review flags disagree")
        if selected and (
            self.unknown
            or not self.top_k
            or self.top_k[0].code != self.final_intent_code
            or self.reason != "calibrated_top1"
        ):
            raise ValueError("Only explained top1 actionable takeover is allowed")
        return self


class FallbackResult(DTO):
    code: IntentCode | None
    reason: Literal["supported", "unknown", "multiple"]
    source: Literal["structured", "fasttext"] = "structured"
    fasttext: FastTextPrediction | None = None

    @model_validator(mode="after")
    def consistent_choice(self) -> FallbackResult:
        if (self.reason == "supported") != (self.code is not None):
            raise ValueError("Fallback reason and code disagree")
        if self.code is not None and not self.code.actionable:
            raise ValueError("Fallback needs an actionable registry code")
        if self.source == "fasttext" and (
            self.fasttext is None
            or self.fasttext.need_review
            or self.fasttext.final_intent_code != self.code
        ):
            raise ValueError("FastText fallback must match its validated prediction")
        return self


class CascadeStep(DTO):
    layer: Literal["rule", "current", "memory", "fallback", "context"]
    action: Literal["accept", "continue", "clarify", "handoff", "reject"]
    reason: Identifier
    retrieval: DenseResult | None = None
    retrieval_calls: Count = 0
    model_calls: Count = 0
    fasttext: FastTextPrediction | None = None


class IntentDecision(DTO):
    decision: Decision
    final_code: IntentCode | None = None
    candidates: tuple[IntentCandidate, ...] = ()
    available_slots: tuple[EntityName, ...] = ()
    stage_evidence: tuple[EvidenceRef, ...] = ()
    reason_code: Identifier
    registry_version: Literal["complaints-v1"] = REGISTRY_VERSION
    policy_version: Literal["slot-policy-v1", "cascade-policy-v1"] = POLICY_VERSION
    override: IntentOverride | None = None
    retrieval: DenseResult | None = None
    cascade_steps: tuple[CascadeStep, ...] = Field(default=(), max_length=5)

    @model_validator(mode="after")
    def selection_policy(self) -> IntentDecision:
        codes = [candidate.code for candidate in self.candidates]
        if len(codes) != len(set(codes)):
            raise ValueError("Duplicate candidate code")
        if [candidate.rank for candidate in self.candidates] != list(range(1, len(codes) + 1)):
            raise ValueError("Candidates must be explicitly ranked starting at 1")
        if len(self.available_slots) != len(set(self.available_slots)):
            raise ValueError("Duplicate available slot")
        if self.decision == Decision.ACCEPT and self.final_code is None:
            raise ValueError("Accept needs a final code")
        if self.final_code is None:
            if self.override is not None:
                raise ValueError("Override needs a selection")
            return self
        if self.final_code not in codes:
            raise ValueError("Final selection must be an actionable candidate")
        slots = set(self.available_slots)
        complete = set(self.final_code.required_slots) <= slots
        if self.decision == Decision.ACCEPT and not complete:
            raise ValueError("Missing required slots must clarify, not accept")
        if self.final_code != codes[0]:
            if self.override is None:
                raise ValueError("Non-top1 selection requires a registered override")
            if set(codes[0].required_slots) <= slots or not complete:
                raise ValueError("Override must prove top1 missing slots and selected complete")
            if not all(ref.source == EvidenceSource.MESSAGE for ref in self.override.evidence_refs):
                raise ValueError("Slot override needs message evidence")
        elif self.override is not None:
            raise ValueError("Top1 selection does not use an override")
        return self

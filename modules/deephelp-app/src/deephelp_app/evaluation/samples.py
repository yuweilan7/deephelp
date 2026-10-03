"""Frozen synthetic development cases; validation is not classification or tool execution."""

import json
from datetime import UTC, datetime
from importlib.resources import files
from typing import Literal

from pydantic import Field, model_validator

from deephelp_app.demo.fixtures import BusinessFixtures, load_business_fixtures
from deephelp_app.domain.checks import response_from_sop
from deephelp_app.domain.models import (
    DTO,
    BudgetUsed,
    ConverseInput,
    Entity,
    EntityName,
    ErrorCode,
    Identifier,
    IntentCode,
    NextAction,
    Outcome,
    QuestionStatus,
    RequestEnvelope,
    SOPResult,
    SOPStatus,
    ToolName,
    ToolParameters,
    VerifiedIdentity,
    VersionManifest,
)
from deephelp_app.domain.registry import complaint_registry


class ExpectedTool(DTO):
    tool_name: ToolName
    parameters: ToolParameters


class CaseExpectation(DTO):
    intent: IntentCode | None
    entities: tuple[Entity, ...]
    tools: tuple[ExpectedTool, ...]
    outcome: Outcome
    question_status: QuestionStatus | None
    missing_slots: tuple[EntityName, ...] = ()
    next_action: NextAction
    error_code: ErrorCode | None = None


class SmokeCase(DTO):
    case_id: Identifier
    synthetic: Literal[True]
    source_group: Identifier
    variant_group: Identifier
    split: Literal["reference", "dev", "regression"]
    scenario: Identifier
    identity: VerifiedIdentity
    message: ConverseInput
    expected: CaseExpectation


class SampleCorpus(DTO):
    version: Literal["smoke-v1"]
    synthetic: Literal[True]
    cases: tuple[SmokeCase, ...] = Field(min_length=30, max_length=60)

    @model_validator(mode="after")
    def isolated_splits(self) -> SampleCorpus:
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate case_id")
        for group_name in ("source_group", "variant_group"):
            groups: dict[str, str] = {}
            for case in self.cases:
                key = getattr(case, group_name)
                if key in groups and groups[key] != case.split:
                    raise ValueError("Source/variant group cannot cross splits")
                groups[key] = case.split
        texts: dict[str, str] = {}
        for case in self.cases:
            if case.message.raw_text in texts and texts[case.message.raw_text] != case.split:
                raise ValueError("Duplicate text cannot cross splits")
            texts[case.message.raw_text] = case.split
        return self


def load_corpus() -> SampleCorpus:
    text = (
        files("deephelp_app").joinpath("assets/evaluation/cases.json").read_text(encoding="utf-8")
    )
    return SampleCorpus.model_validate_json(text)


def validate_corpus(corpus: SampleCorpus, business: BusinessFixtures) -> None:
    """Check gold labels and references; never pretend to predict the expected answers."""
    registry = complaint_registry()
    orders = {order.order_id: order for order in business.orders}
    coupons = {coupon.coupon_id: coupon for coupon in business.coupons}
    for case in corpus.cases:
        expected = case.expected
        values = {entity.name.value: entity.value for entity in expected.entities}
        if len(values) != len(expected.entities):
            raise ValueError("Duplicate expected slot")
        for entity in expected.entities:
            if entity.source.message_id != case.message.message_id:
                raise ValueError("Expected entity needs this message as source")
            if (
                entity.value not in entity.source.excerpt
                or entity.source.excerpt not in case.message.raw_text
            ):
                raise ValueError("Expected entity must preserve exact original text")
        if expected.intent is None:
            if expected.tools or expected.outcome not in {
                Outcome.HANDOFF,
                Outcome.REJECTED,
                Outcome.CLARIFY,
            }:
                raise ValueError("Unknown intent cannot call tools")
            continue
        definition = registry.get(expected.intent)
        if not definition.is_actionable:
            raise ValueError("Expected intent must be actionable")
        missing = {slot.value for slot in definition.required_slots} - set(values)
        if set(expected.missing_slots) != missing:
            raise ValueError("Missing-slot expectation must match registry")
        if missing:
            if (
                expected.outcome != Outcome.CLARIFY
                or expected.question_status != QuestionStatus.WAITING_SLOT
                or expected.tools
                or expected.next_action != NextAction.PROVIDE_SLOTS
            ):
                raise ValueError("Missing slots require CLARIFY / WAITING_SLOT / tools 0")
        elif expected.outcome == Outcome.ANSWERED:
            if expected.question_status != QuestionStatus.RESOLVED or len(expected.tools) != 1:
                raise ValueError("Answered smoke case needs one read-only lookup")
            for tool in expected.tools:
                correct_tool = (
                    ToolName.CHECK_COUPON
                    if expected.intent == IntentCode.COUPON_UNUSABLE
                    else ToolName.GET_ORDER_BENEFITS
                )
                if tool.tool_name != correct_tool or tool.parameters.order_id != values["order_id"]:
                    raise ValueError("Tool name/order must match intent and exact entities")
                order = orders.get(tool.parameters.order_id)
                if order is None or order.identity != case.identity:
                    raise ValueError("Tool fixture must belong to the case owner")
                if tool.tool_name == ToolName.CHECK_COUPON:
                    coupon = coupons.get(tool.parameters.coupon_id or "")
                    if (
                        coupon is None
                        or coupon.identity != case.identity
                        or coupon.order_id != order.order_id
                        or coupon.coupon_id != values["coupon_id"]
                    ):
                        raise ValueError("Coupon parameters must match the owned fixture")
                elif tool.parameters.coupon_id is not None:
                    raise ValueError("Order tool cannot receive a coupon parameter")
        elif expected.outcome == Outcome.REJECTED:
            order = orders.get(values["order_id"])
            if (
                order is None
                or order.identity == case.identity
                or expected.error_code != ErrorCode.FORBIDDEN
                or expected.tools
            ):
                raise ValueError("Cross-owner case must be rejected with tools 0")
        else:
            raise ValueError("Unsupported labeled smoke closeout")


def main() -> None:
    corpus, business = load_corpus(), load_business_fixtures()
    validate_corpus(corpus, business)
    counts = {
        split: sum(case.split == split for case in corpus.cases)
        for split in ("reference", "dev", "regression")
    }
    missing = next(case for case in corpus.cases if case.scenario == "discount-missing-order")
    request = RequestEnvelope(
        **missing.message.model_dump(),
        identity=missing.identity,
        request_id="demo-request",
        trace_id="demo-trace",
        received_at=datetime.now(UTC),
    )
    response = response_from_sop(
        request,
        run_id="demo-run",
        question_id="demo-question",
        result=SOPResult(
            status=SOPStatus.WAITING_SLOT,
            sop_id="discount-missing-v1",
            sop_version="synthetic-v1",
            missing_slots=(EntityName.ORDER_ID,),
            next_action=NextAction.PROVIDE_SLOTS,
        ),
        reply="请提供订单号。",
        versions=VersionManifest(
            registry="complaints-v1", dataset=corpus.version, split="dev", policy="slot-policy-v1"
        ),
        budget_used=BudgetUsed(),
    )
    print(
        json.dumps(
            {
                "validation": "PASS",
                "contract": "0.2.0-m02",
                "cases": len(corpus.cases),
                "splits": counts,
                "actionable_intents": [
                    code.value for code in complaint_registry().actionable_codes
                ],
                "contract_closeout": {
                    "case_id": missing.case_id,
                    "outcome": response.outcome,
                    "question_status": response.question_status,
                    "run_status": response.run_status,
                    "tool_calls": len(response.tool_call_ids),
                },
                "executed_business_tools": 0,
                "model_calls": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

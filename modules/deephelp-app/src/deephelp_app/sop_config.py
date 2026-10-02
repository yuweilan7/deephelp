"""Small declarative read-only SOP format; no expressions or executable configuration."""

import json
from importlib.resources import files
from typing import Literal

from jsonschema import Draft202012Validator
from pydantic import Field, model_validator

from deephelp_app.domain.models import (
    DTO,
    EntityName,
    Identifier,
    IntentCode,
    SOPStatus,
    ToolName,
)
from deephelp_app.domain.registry import complaint_registry

PROMPT_VERSION = "sop-react-v1"
STOP_CONDITIONS = (
    "missing_slots",
    "invalid_action",
    "repeated_call",
    "deadline",
    "max_steps",
    "max_tools",
)
HANDOFF_CONDITIONS = ("model_handoff", "insufficient_evidence", "unmatched_branch")


class SOPLimits(DTO):
    max_steps: int = Field(default=4, strict=True, ge=1, le=10)
    max_tool_calls: int = Field(default=2, strict=True, ge=1, le=8)
    total_seconds: float = Field(default=60, gt=0, le=120, allow_inf_nan=False)
    model_seconds: float = Field(default=30, gt=0, le=60, allow_inf_nan=False)
    tool_seconds: float = Field(default=5, gt=0, le=30, allow_inf_nan=False)
    retries_per_call: int = Field(default=1, strict=True, ge=0, le=2)
    total_retries: int = Field(default=1, strict=True, ge=0, le=4)


class SOPCondition(DTO):
    fact: Identifier
    op: Literal["eq", "empty", "nonempty"]
    value: str | bool | None = None

    @model_validator(mode="after")
    def operand(self) -> SOPCondition:
        if (self.op == "eq") != (self.value is not None):
            raise ValueError("Only equality has an explicit operand")
        return self


class SOPBranch(DTO):
    condition: SOPCondition
    status: Literal[SOPStatus.RESOLVED, SOPStatus.HANDED_OFF]
    conclusion: str = Field(min_length=1, max_length=1000)


class SOPDefinition(DTO):
    schema_version: Literal["m07-sop-v1"] = "m07-sop-v1"
    sop_id: Identifier
    version: Identifier
    intent_code: IntentCode
    prompt_version: Literal["sop-react-v1"] = "sop-react-v1"
    tool_version: Literal["m06-readonly-v1"] = "m06-readonly-v1"
    required_slots: tuple[EntityName, ...]
    allowed_tools: tuple[ToolName, ...] = Field(min_length=1, max_length=1)
    steps: tuple[Literal["lookup", "evaluate"], ...]
    required_facts: tuple[Identifier, ...] = Field(min_length=1)
    evidence_version: Literal["business-fixtures-v1"]
    branches: tuple[SOPBranch, ...] = Field(min_length=1, max_length=8)
    stop_conditions: tuple[str, ...]
    handoff_conditions: tuple[str, ...]
    limits: SOPLimits = Field(default_factory=SOPLimits)

    @model_validator(mode="after")
    def compatible(self) -> SOPDefinition:
        item = complaint_registry().get(self.intent_code)
        expected = (
            ToolName.CHECK_COUPON
            if self.intent_code == IntentCode.COUPON_UNUSABLE
            else ToolName.GET_ORDER_BENEFITS
        )
        if (
            not item.is_actionable
            or self.sop_id != item.sop_id
            or self.version != self.sop_id
            or self.required_slots != item.required_slots
            or self.allowed_tools != (expected,)
            or self.steps != ("lookup", "evaluate")
            or self.stop_conditions != STOP_CONDITIONS
            or self.handoff_conditions != HANDOFF_CONDITIONS
            or len(set(self.required_facts)) != len(self.required_facts)
            or any(b.condition.fact not in self.required_facts for b in self.branches)
        ):
            raise ValueError("SOP does not match the published read-only registry or steps")
        conditions = [b.condition.model_dump_json() for b in self.branches]
        if len(set(conditions)) != len(conditions):
            raise ValueError("Duplicate SOP branch")
        return self


def load_sops() -> dict[IntentCode, SOPDefinition]:
    root = files("deephelp_app").joinpath("sop_data")
    schema = json.loads(root.joinpath("schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    definitions = {}
    for name in ("discount-missing-v1", "coupon-unusable-v1", "order-activity-v1"):
        data = json.loads(root.joinpath(name + ".json").read_text(encoding="utf-8"))
        validator.validate(data)
        definition = SOPDefinition.model_validate(data)
        if definition.intent_code in definitions:
            raise ValueError("Duplicate SOP intent")
        definitions[definition.intent_code] = definition
    return definitions


def load_prompt() -> str:
    return files("deephelp_app").joinpath("sop_data/sop-react-v1.txt").read_text(encoding="utf-8")

"""Trusted synthetic service configuration, never model tool arguments."""

from typing import Literal

from pydantic import Field

from deephelp_app.domain.models import DTO
from deephelp_tools.demo.fixtures import BusinessFixtures


class FaultSpec(DTO):
    kind: Literal[
        "normal",
        "upstream_500",
        "rate_limit",
        "missing_field",
        "contradictory",
        "injection",
        "oversized",
    ] = "normal"
    delay_seconds: float = Field(default=0, ge=0, le=60, allow_inf_nan=False)


class MockConfig(DTO):
    faults: dict[str, FaultSpec] = Field(default_factory=dict, max_length=32)
    max_invocations: int = Field(default=256, ge=1, le=4096)
    tool_faults: dict[str, FaultSpec] = Field(default_factory=dict, max_length=32)
    fixtures: BusinessFixtures | None = None

"""显式合成工具资料与故障配置，供demo进程读取。

默认只读固定事实，故障需明确选择；配置不是模型可编写的任意工具脚本。
"""

from typing import Literal

from pydantic import Field

from deephelp_app.domain.models.common import DTO
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

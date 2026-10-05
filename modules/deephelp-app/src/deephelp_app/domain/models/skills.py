"""原run中的Skill执行回执；只保存机器契约绑定、可信输入与实际工具观察。

Skill不是SOP终态或审批授权。成功引用原call/证据，取消/未知不能伪装为成功。
阅读入口：application/skills.py的SkillSession.lookup，再看SOPExecutor._flow。
"""

from typing import Literal

from pydantic import AwareDatetime, model_validator

from .common import DTO, ErrorDetail, Identifier
from .entities import Entity
from .tools import ToolResult, ToolStatus


class SkillReceipt(DTO):
    """一次受控执行的值快照，不拥有客户端、预算或恢复令牌。"""

    execution_id: Identifier
    parent_id: Identifier | None = None
    run_id: Identifier
    question_id: Identifier
    node_id: Identifier
    registry_digest: Identifier
    skill_id: Identifier
    version: Identifier
    digest: Identifier
    slots: tuple[Entity, ...]
    status: Literal["RUNNING", "COMPLETED", "FAILED", "CANCELLED", "UNKNOWN"]
    observation: ToolResult | None = None
    error: ErrorDetail | None = None
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def supported(self) -> SkillReceipt:
        if self.status == "COMPLETED" and (
            not self.observation
            or self.observation.status != ToolStatus.SUCCEEDED
            or not self.observation.evidence_refs
            or not self.observation.call_id
        ):
            raise ValueError("Completed Skill requires an actual evidenced tool observation")
        if self.observation and self.observation.run_id != self.run_id:
            raise ValueError("Skill observation belongs to another run")
        return self

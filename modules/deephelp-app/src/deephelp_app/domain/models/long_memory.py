"""跨会话记忆的来源、授权范围与选择回执；值对象不拥有连接。

记忆是派生上下文，source_hash绑定原问题完整字节语义，不能当成当前业务事实。
MemoryUse必须选择精确版本，scope_revision防止旧页面在授权范围改变后继续使用。
"""

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .common import DTO, Count, Identifier, PositiveCount, VerifiedIdentity


class MemoryChange(DTO):
    """明确操作正文；edit_id是用户更正来源，不能作为业务事实证据。"""

    expected_revision: PositiveCount
    action: Literal["retain", "correct", "revoke", "forget"]
    edit_id: Identifier
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    confidence: Literal["USER_ASSERTED", "LOW"] = "USER_ASSERTED"
    valid_days: int = Field(default=30, ge=1, le=365)

    @model_validator(mode="after")
    def explicit_content(self) -> MemoryChange:
        if (self.action in {"retain", "correct"}) != (self.text is not None):
            raise ValueError("Only explicit retain/correct needs text")
        return self


class MemoryReference(DTO):
    memory_id: Identifier
    revision: PositiveCount


class MemoryUse(DTO):
    scope_revision: PositiveCount
    references: tuple[MemoryReference, ...] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def unique_references(self) -> MemoryUse:
        if len({r.memory_id for r in self.references}) != len(self.references):
            raise ValueError("Memory references must be unique")
        return self


class MemoryScope(DTO):
    owner: VerifiedIdentity
    target_session: Identifier
    revision: Count = 0
    enabled: bool = False
    source_sessions: tuple[Identifier, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def cross_session(self) -> MemoryScope:
        if self.target_session in self.source_sessions or len(set(self.source_sessions)) != len(
            self.source_sessions
        ):
            raise ValueError("Memory scope must contain unique other sessions")
        if self.enabled and not self.source_sessions:
            raise ValueError("Enabled memory needs explicit sources")
        return self


class LongMemory(DTO):
    memory_id: Identifier
    owner: VerifiedIdentity
    revision: PositiveCount = 1
    kind: Literal["closed_summary", "user_retained"] = "closed_summary"
    state: Literal["ACTIVE", "REVOKED", "FORGOTTEN"] = "ACTIVE"
    source_session: Identifier
    source_question: Identifier
    source_messages: tuple[Identifier, ...] = Field(min_length=1)
    source_version: PositiveCount
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_ids: tuple[Identifier, ...] = ()
    text: str = Field(min_length=1, max_length=3000)
    created_at: AwareDatetime
    expires_at: AwareDatetime
    retained: bool = False
    confidence: Literal["SOURCE_VERIFIED", "USER_ASSERTED", "LOW"] = "SOURCE_VERIFIED"
    edit_id: Identifier | None = None
    changed_at: AwareDatetime | None = None
    change_action: Literal["preserve", "retain", "correct", "revoke", "forget"] = "preserve"


class MemoryDecision(DTO):
    reference: MemoryReference
    status: Literal["ADOPTED", "REJECTED"]
    reason: Identifier
    memory: LongMemory | None = None


class MemorySelection(DTO):
    strategy: Literal["m46-explicit-v1"] = "m46-explicit-v1"
    target_session: Identifier
    scope_revision: Count = 0
    status: Literal["DISABLED", "EMPTY", "SELECTED", "REJECTED", "UNAVAILABLE", "CANCELLED"]
    decisions: tuple[MemoryDecision, ...] = ()
    context_bytes: Count = 0
    model_calls: Literal[0] = 0

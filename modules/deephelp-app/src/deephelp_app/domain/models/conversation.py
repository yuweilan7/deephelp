"""入口消息与问题的唯一DTO，API、主链和仓储共用。

ConverseInput是未可信正文，身份由入口另注入；Question保存成员、实体来源与绑定版本。
校验拒绝跨成员来源、重复实体及不完整审批状态；model_copy后需要按边界重新验证。
"""

from __future__ import annotations

from typing import Literal

from pydantic import (
    AwareDatetime,
    Field,
    model_validator,
)

from .common import (
    CONTRACT_VERSION,
    DTO,
    REGISTRY_VERSION,
    Identifier,
    PositiveCount,
    QuestionStatus,
    RawText,
    VerifiedIdentity,
)
from .entities import Entity, EntityConflict, EntityName
from .intents import IntentCode
from .long_memory import MemoryUse
from .release import VersionManifest


class ConverseInput(DTO):
    """Untrusted message content; identity/request/trace IDs come from the entry point."""

    channel: Identifier
    session_id: Identifier
    message_id: Identifier
    raw_text: RawText
    occurred_at: AwareDatetime
    question_hint: Identifier | None = None
    expected_question_version: PositiveCount | None = None
    execution_mode: Literal["single", "collaboration"] = "single"
    history_memory: MemoryUse | None = None

    @model_validator(mode="after")
    def continuation_version(self) -> ConverseInput:
        if self.expected_question_version is not None and self.question_hint is None:
            raise ValueError("Question version requires a question hint")
        return self


class RequestEnvelope(ConverseInput):
    schema_version: Literal["0.2.0-m02"] = CONTRACT_VERSION
    identity: VerifiedIdentity
    request_id: Identifier
    trace_id: Identifier
    received_at: AwareDatetime


class Question(DTO):
    question_id: Identifier
    identity: VerifiedIdentity
    session_id: Identifier
    status: QuestionStatus
    version: PositiveCount
    entities: tuple[Entity, ...] = ()
    conflicts: tuple[EntityConflict, ...] = ()
    unresolved_fields: tuple[EntityName, ...] = ()
    event_summary: str = Field(default="", max_length=4000)
    aggregation_pending: bool = False
    member_message_ids: tuple[Identifier, ...] = Field(min_length=1)
    active_intent: IntentCode | None = None
    versions: VersionManifest
    created_at: AwareDatetime
    updated_at: AwareDatetime
    approval_operation_id: Identifier | None = None

    @model_validator(mode="after")
    def provenance_and_versions(self) -> Question:
        from deephelp_app.domain.registry import complaint_registry

        names = [entity.name for entity in self.entities]
        if len(names) != len(set(names)):
            raise ValueError(
                "Current entities have one value per slot; retain conflicts separately"
            )
        sources = [entity.source.message_id for entity in self.entities]
        for index, conflict in enumerate(self.conflicts):
            sources.extend(
                [conflict.previous.source.message_id, conflict.replacement.source.message_id]
            )
            if (
                conflict.resolution == "explicit_correction"
                and not any(
                    current.name == conflict.replacement.name
                    and current.value == conflict.replacement.value
                    for current in self.entities
                )
                and not any(
                    later.previous == conflict.replacement for later in self.conflicts[index + 1 :]
                )
            ):
                raise ValueError("Corrected value must be the current entity")
        if not set(sources) <= set(self.member_message_ids):
            raise ValueError("Entity source must belong to this question")
        if self.updated_at < self.created_at:
            raise ValueError("Update cannot precede creation")
        if self.active_intent is not None:
            if (
                not self.active_intent.actionable
                or self.active_intent
                not in complaint_registry(
                    self.versions.registry or REGISTRY_VERSION
                ).actionable_codes
                or self.versions.sop is None
            ):
                raise ValueError("Active intent needs pinned compatible registry/SOP versions")
        if self.status == QuestionStatus.WAITING_APPROVAL and not self.approval_operation_id:
            raise ValueError("Waiting approval requires a bound operation")
        return self


class LifecycleCommand(DTO):
    session_id: Identifier
    expected_version: PositiveCount
    target: QuestionStatus
    reason: RawText
    evidence_source: Literal[
        "user_confirmation",
        "process_result",
        "explicit_reopen",
        "operator_cancel",
        "operator_handoff",
        "slot_check",
    ]
    evidence_ref: Identifier

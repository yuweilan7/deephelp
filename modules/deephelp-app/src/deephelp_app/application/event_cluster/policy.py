"""Event membership policy; never classify main intent or execute SOP tools."""

import re
from dataclasses import dataclass
from typing import Protocol

from deephelp_app.adapters.ledger import Receipt
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import (
    VerifiedIdentity,
)
from deephelp_app.domain.models.conversation import Question, RequestEnvelope
from deephelp_app.domain.models.events import (
    EventCandidate,
)
from deephelp_app.domain.models.responses import ResponseEnvelope
from deephelp_app.domain.ports import CaseRepository

CORRECTION = re.compile(
    r"更正|改为|改成|不是.+[，,].*(?:而是|是)|不是.+而是|说错了[，,、\s]*(?:是|应为)|不对[，,\s]*是"
)


SUPPLEMENT = re.compile(
    r"^(?:补充|刚才|刚刚|说错|更正|改为|券(?:号|是)|订单(?:号|是)|SKU)|^\d{4,32}$"
)


NEW_TOPIC = re.compile(r"另外|换个话题|新问题|还有一个|还有一单")


SPLIT = re.compile(r"[；;。，,]|(?:另外|同时|还有|以及)(?=.{0,40}(?:订单|优惠|券|活动))")


@dataclass(frozen=True)
class ClusterPolicy:
    window_count: int = 30
    window_bytes: int = 8192
    age_seconds: int = 86400
    case_limit: int = 32
    top_k: int = 5
    summary_chars: int = 2000
    judge_timeout: float = 45
    min_similarity: float = 0.15

    def __post_init__(self) -> None:
        if not (
            1 <= self.window_count <= 100
            and 1024 <= self.window_bytes <= 32768
            and 1 <= self.case_limit <= 64
            and 1 <= self.top_k <= 12
            and 256 <= self.summary_chars <= 4000
            and 0 < self.judge_timeout <= 60
            and 0 < self.age_seconds <= 604800
            and -1 <= self.min_similarity <= 1
        ):
            raise ValueError("Invalid event cluster bounds")


class EventSimilarityPort(Protocol):
    async def candidates(
        self,
        identity: VerifiedIdentity,
        session: str,
        query: str,
        budget: ExecutionBudget,
        *,
        top_k: int,
    ) -> tuple[EventCandidate, ...]: ...


class EventLedger(CaseRepository, Protocol):
    async def lookup(self, request: RequestEnvelope) -> Receipt | None: ...
    async def accept(
        self, request: RequestEnvelope, *, attribution: tuple[str, int] | None = None
    ) -> Receipt: ...
    async def finish(
        self, receipt: Receipt, question: Question, response: ResponseEnvelope
    ) -> None: ...

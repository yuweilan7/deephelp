"""从仓储读取同身份、同session的有界归属窗口。

MessageWindowAssembler复验窗口中问题的归属，发现混入外主体即拒绝。
它不凭相似度扩大权限；裁剪过的窗口由aggregation按证据不足处理。
"""

from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import (
    ErrorCode,
)
from deephelp_app.domain.models.conversation import RequestEnvelope
from deephelp_app.domain.models.memory import MemoryWindow
from deephelp_app.domain.ports import CaseRepository

from .policy import ClusterPolicy


class MessageWindowAssembler:
    def __init__(self, repository: CaseRepository, policy: ClusterPolicy) -> None:
        self.repository, self.policy = repository, policy

    async def load(self, request: RequestEnvelope) -> MemoryWindow:
        p = self.policy
        window = await self.repository.snapshot(
            request.identity,
            request.session_id,
            count=p.window_count,
            token_limit=p.window_bytes,
            age_seconds=p.age_seconds,
            case_limit=p.case_limit,
        )
        # Defend the Port boundary too; a broken cache/adapter cannot create cross-scope edges.
        if any(
            q.identity != request.identity or q.session_id != request.session_id
            for q in window.active_questions
        ):
            raise AppError(ErrorCode.FORBIDDEN, "Event window scope mismatch")
        return window

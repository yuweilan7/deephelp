"""固定合成动作回放，供SOP确定性回归复用原执行器。

fixture指定观察序列，不自由编写事实；回放只验证守卫和收口，不证明真实工具选择质量。
"""

import json
from collections.abc import Sequence

from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.model_io import ChatRequest, ChatResult, ModelToolCall, ModelUsage
from deephelp_tools.assets import asset_path


class ReplayModel:
    def __init__(self, actions: Sequence[ModelToolCall]) -> None:
        self.actions = list(actions)
        self.requests: list[ChatRequest] = []

    @classmethod
    def fixture(cls, name: str) -> ReplayModel:
        data = json.loads(asset_path("model-replay-v1.json").read_text(encoding="utf-8"))
        if data["synthetic"] is not True or data["version"] != "m07-model-replay-v1":
            raise ValueError("Unknown synthetic SOP replay")
        return cls([ModelToolCall.model_validate(a) for a in data["cases"][name]])

    async def chat(self, request: ChatRequest, budget: ExecutionBudget) -> ChatResult:
        budget.claim_attempt(retry=False)
        self.requests.append(request.model_copy(deep=True))
        if not self.actions:
            raise AssertionError("Synthetic SOP model fixture exhausted")
        return ChatResult(
            tool_calls=[self.actions.pop(0)],
            usage=ModelUsage(),
            model="synthetic-replay",
            finish_reason="tool_calls",
        )

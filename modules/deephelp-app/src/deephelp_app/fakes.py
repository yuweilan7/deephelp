import asyncio

from deephelp_app.domain.models import RequestEnvelope, ResponseEnvelope, VerifiedIdentity
from deephelp_app.execution import AsyncCalls, ExecutionBudget


class FakeModelGateway:
    """Synthetic fixed output. Never represented as a real model result."""

    def __init__(self, calls: AsyncCalls, delay: float = 0) -> None:
        self.calls = calls
        self.delay = delay
        self.call_count = 0
        self.active = 0
        self.peak_active = 0
        self.cleaned = 0

    async def complete(self, text: str, budget: ExecutionBudget) -> str:
        async def operation() -> str:
            self.call_count += 1
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            try:
                await asyncio.sleep(self.delay)
                return "FAKE_MODEL_OUTPUT"
            finally:
                self.active -= 1
                self.cleaned += 1

        return await self.calls.call(operation, budget, retry_safe=True)


class FakeRepository:
    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str, str], ResponseEnvelope] = {}

    async def get(
        self, identity: VerifiedIdentity, channel: str, message_id: str
    ) -> ResponseEnvelope | None:
        result = self._values.get((identity.tenant_id, identity.user_id, channel, message_id))
        return None if result is None else result.model_copy(deep=True)

    async def put(self, request: RequestEnvelope, response: ResponseEnvelope) -> None:
        key = (
            request.identity.tenant_id,
            request.identity.user_id,
            request.channel,
            request.message_id,
        )
        self._values[key] = response.model_copy(deep=True)

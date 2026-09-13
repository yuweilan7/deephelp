from typing import Protocol

from deephelp_app.domain.models import RequestEnvelope, ResponseEnvelope, VerifiedIdentity
from deephelp_app.execution import ExecutionBudget


class ModelGateway(Protocol):
    async def complete(self, text: str, budget: ExecutionBudget) -> str: ...


class Repository(Protocol):
    """M01 fake storage seam, not a persistent message/idempotency ledger."""

    async def get(
        self, identity: VerifiedIdentity, channel: str, message_id: str
    ) -> ResponseEnvelope | None: ...

    async def put(self, request: RequestEnvelope, response: ResponseEnvelope) -> None: ...

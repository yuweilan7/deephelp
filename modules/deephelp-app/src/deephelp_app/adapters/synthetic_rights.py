"""Owned loopback synthetic rights service. Effects and calls survive service restarts.

No payment/refund integration. Only a signed, approved M15 operation can record a
synthetic adjustment. The downstream effect commits separately from the agent ledger.
"""

import hashlib
import hmac
import json
from typing import Literal

import httpx

from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import AsyncCalls, ExecutionBudget
from deephelp_app.domain.models import (
    DTO,
    ErrorCode,
    Identifier,
    OperationRecord,
)


def binding_hash(op: OperationRecord) -> str:
    data = {
        "plan": op.plan.model_dump(mode="json"),
        "run_id": op.run_id,
        "session_id": op.session_id,
        "question_version": op.question_version,
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def signature(key: bytes, kind: str, operation: str, digest: str) -> str:
    return hmac.new(key, f"{kind}:{operation}:{digest}".encode(), hashlib.sha256).hexdigest()


class RightsRequest(DTO):
    operation_id: Identifier
    binding_hash: str
    signature: str


class SyntheticEffect(DTO):
    operation_id: Identifier
    binding_hash: str
    call_id: Identifier
    order_id: Identifier
    status: Literal["applied"] = "applied"
    synthetic: Literal[True] = True


class RightsObservation(DTO):
    status: Literal["ABSENT", "SUCCEEDED"]
    effect: SyntheticEffect | None = None


class RightsClient:
    def __init__(self, client: httpx.AsyncClient, port: int, key: bytes) -> None:
        if not 1 <= port <= 65535 or len(key) != 32:
            raise ValueError("Loopback port and a 32-byte service key are required")
        self.client, self.port, self.key = client, port, key
        self.calls = AsyncCalls(2, 10)

    async def call(
        self, kind: Literal["execute", "query"], op: OperationRecord, budget: ExecutionBudget
    ) -> RightsObservation:
        digest = binding_hash(op)
        body = RightsRequest(
            operation_id=op.plan.operation_id,
            binding_hash=digest,
            signature=signature(self.key, kind, op.plan.operation_id, digest),
        )

        async def send() -> RightsObservation:
            reply = await self.client.post(
                f"http://127.0.0.1:{self.port}/{kind}", json=body.model_dump()
            )
            if reply.status_code != 200:
                raise AppError(
                    ErrorCode.UPSTREAM_UNAVAILABLE,
                    f"Synthetic operation HTTP {reply.status_code} needs reconciliation",
                    503,
                )
            observation = RightsObservation.model_validate(reply.json())
            effect = observation.effect
            if observation.status == "SUCCEEDED" and (
                effect is None
                or effect.operation_id != op.plan.operation_id
                or effect.binding_hash != digest
                or effect.order_id != op.plan.parameters.order_id
            ):
                raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Synthetic result binding differs")
            if observation.status == "ABSENT" and (kind != "query" or effect is not None):
                raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Invalid synthetic observation")
            return observation

        return await self.calls.call(send, budget, retry_safe=False)

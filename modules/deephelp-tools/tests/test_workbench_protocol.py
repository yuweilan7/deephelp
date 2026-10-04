"""Deterministic guards; real protocol/checkpoint content is accepted separately."""

from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from deephelp_app.adapters.trace import MemoryTrace
from deephelp_app.api.app import create_app
from deephelp_app.bootstrap.settings import Settings
from deephelp_app.domain.models import VerifiedIdentity
from deephelp_tools.learning.workbench_protocol import ProtocolAction, learning_router

pytestmark = pytest.mark.integration


def test_fixed_actions_and_no_arbitrary_arguments():
    for body in [
        {"action": "shell"},
        {"action": "benefits", "tool": "refund"},
        {"action": "benefits", "order_id": 123},
        {"action": "benefits", "sql": "x"},
    ]:
        with pytest.raises(ValidationError):
            ProtocolAction.model_validate(body)


async def test_learning_guards_and_client_reject_without_dispatch():
    identity = VerifiedIdentity(tenant_id="personal", user_id="owner")
    app = create_app(Settings(mode="test"), trace=MemoryTrace())
    assembly = SimpleNamespace(ledger=object(), tools=object())
    app.include_router(learning_router(assembly, lambda r: identity))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        assert (await client.get("/learning/status")).json()["demo_enabled"] is False
        assert (await client.post("/learning/demo")).status_code == 403
        assert (
            await client.post("/learning/protocol", json={"action": "shell"})
        ).status_code == 422
        result = (await client.post("/learning/protocol", json={"action": "client_invalid"})).json()
        assert result["rejected"] and not result["dispatched"] and result["model_calls"] == 0
        assert result["trace_id"]


def test_invalid_demo_key_rejected_at_assembly():
    with pytest.raises(ValueError):
        learning_router(SimpleNamespace(), lambda r: None, rights_port=8024, rights_key=b"short")

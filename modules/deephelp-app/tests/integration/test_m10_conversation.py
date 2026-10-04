from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from deephelp_app.adapters.trace import MemoryTrace
from deephelp_app.api.app import create_app
from deephelp_app.bootstrap.settings import Settings
from deephelp_app.domain.models import VerifiedIdentity
from deephelp_tools.learning.cases_fake import MemoryCaseRepository
from deephelp_tools.learning.mvp_replay import ReplayAssembly

pytestmark = pytest.mark.integration


@asynccontextmanager
async def client():
    repo = MemoryCaseRepository()
    assembly = ReplayAssembly(ledger=repo)
    identity = VerifiedIdentity(tenant_id="synthetic-tenant", user_id="synthetic-user-a")
    app = create_app(
        Settings(mode="test", request_timeout=60, max_attempts=40),
        trace=MemoryTrace(),
        identity_provider=lambda r: identity,
        conversation_factory=assembly.open,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://local") as api,
    ):
        yield api, repo


def message(text, hint=None, version=None):
    return dict(
        channel="m10",
        session_id="s",
        message_id=uuid4().hex,
        raw_text=text,
        occurred_at=datetime.now(UTC).isoformat(),
        question_hint=hint,
        expected_question_version=version,
    )


async def test_interleaved_slot_completion_preserves_other_question_and_replays():
    async with client() as (api, repo):
        a = (await api.post("/converse", json=message("我的订单未享受优惠，请查一下"))).json()
        b = (await api.post("/converse", json=message("订单 000042 的券不能用"))).json()
        assert a["question_status"] == b["question_status"] == "WAITING_SLOT"
        window = (await api.get("/memory", params={"session_id": "s"})).json()
        assert len(window["active_questions"]) == 2
        body = message("订单 000031", a["question_id"], 2)
        completed = (await api.post("/converse", json=body)).json()
        assert (
            completed["question_id"] == a["question_id"]
            and completed["question_status"] == "RESOLVED"
        )
        assert completed["intent_decision"]["reason_code"] == "explicit_question_context"
        assert "99.90" in completed["reply"] and completed["tool_call_ids"]
        replay = (await api.post("/converse", json=body)).json()
        assert replay["replayed"] and replay["budget_used"]["attempts"] == 0
        assert repo.questions[b["question_id"]].status == "WAITING_SLOT"


async def test_correction_and_conflict_keep_original_source_and_block_tools():
    async with client() as (api, repo):
        first = (await api.post("/converse", json=message("订单 000042 的券不能用"))).json()
        qid = first["question_id"]
        conflict = (await api.post("/converse", json=message("订单 000053", qid, 2))).json()
        assert conflict["question_status"] == "WAITING_SLOT" and not conflict["tool_call_ids"]
        q = repo.questions[qid]
        assert q.entities[0].value == "000042" and q.unresolved_fields
        corrected = (
            await api.post("/converse", json=message("更正：订单 000031", qid, q.version))
        ).json()
        assert corrected["question_status"] == "WAITING_SLOT"
        q = repo.questions[qid]
        assert (
            q.entities[0].value == "000031" and q.conflicts[-1].resolution == "explicit_correction"
        )
        assert q.conflicts[-1].previous.source.message_id == q.member_message_ids[0]
        second = (
            await api.post("/converse", json=message("更正：订单 000042", qid, q.version))
        ).json()
        assert (
            second["question_status"] == "WAITING_SLOT" and len(repo.questions[qid].conflicts) == 3
        )
        again = (
            await api.post(
                "/converse", json=message("更正：订单 000042", qid, repo.questions[qid].version)
            )
        ).json()
        assert again["question_status"] == "WAITING_SLOT" and again["error"] is None


async def test_hint_wrong_topic_preserves_pinned_intent_and_no_tools():
    async with client() as (api, repo):
        a = (await api.post("/converse", json=message("我的订单未享受优惠，请查一下"))).json()
        wrong = (
            await api.post(
                "/converse", json=message("查询订单 000031 参加的活动", a["question_id"], 2)
            )
        ).json()
        assert wrong["error"]["code"] == "INVALID_ARGUMENT" and not wrong["tool_call_ids"]
        assert repo.questions[a["question_id"]].active_intent == "DISCOUNT_MISSING"
        assert repo.questions[a["question_id"]].entities == ()


async def test_lifecycle_api_requires_version_evidence_and_explicit_reopen():
    async with client() as (api, repo):
        result = (await api.post("/converse", json=message("我的订单未享受优惠，请查一下"))).json()
        qid = result["question_id"]
        body = dict(
            session_id="s",
            expected_version=2,
            target="CANCELLED",
            reason="用户取消",
            evidence_source="operator_cancel",
            evidence_ref="synthetic-cancel",
        )
        assert (await api.post(f"/questions/{qid}/state", json=body)).status_code == 200
        assert (await api.post(f"/questions/{qid}/state", json=body)).status_code == 409
        assert (await api.post("/converse", json=message("订单000031", qid))).status_code == 409
        body.update(
            expected_version=3,
            target="ACTIVE",
            reason="用户明确要求重开",
            evidence_source="explicit_reopen",
        )
        reopened = await api.post(f"/questions/{qid}/state", json=body)
        assert reopened.status_code == 200 and reopened.json()["version"] == 4

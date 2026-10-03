import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from deephelp_app.app import create_app
from deephelp_app.cases_fake import MemoryCaseRepository
from deephelp_app.domain.models import IntentCandidate, IntentDecision
from deephelp_app.event_replay import ReplayJudge
from deephelp_app.mcp_mock import FaultSpec, MockConfig
from deephelp_app.mvp_replay import ReplayAssembly
from deephelp_app.mvp_runtime import LocalAuth
from deephelp_app.settings import Settings
from deephelp_app.trace import JsonlTrace, MemoryTrace

pytestmark = pytest.mark.integration
TOKEN = "a" * 32


def message(text, session="s", **changes):
    return dict(
        channel="m16",
        session_id=session,
        message_id=uuid4().hex,
        raw_text=text,
        occurred_at=datetime.now(UTC).isoformat(),
        **changes,
    )


@asynccontextmanager
async def client(tmp_path, *, trace=None, tweak=None, mock=None):
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps(
            {
                "tokens": [
                    {
                        "token": TOKEN,
                        "identity": {
                            "tenant_id": "synthetic-tenant",
                            "user_id": "synthetic-user-a",
                        },
                    },
                    {
                        "token": "b" * 32,
                        "identity": {
                            "tenant_id": "synthetic-tenant",
                            "user_id": "synthetic-user-b",
                        },
                    },
                    {
                        "token": "c" * 32,
                        "identity": {"tenant_id": "other-tenant", "user_id": "synthetic-user-a"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    repo, judge = MemoryCaseRepository(), ReplayJudge()
    assembly = ReplayAssembly(ledger=repo, event_judge=judge, mock=mock)
    sink = trace or MemoryTrace()

    @asynccontextmanager
    async def factory(http, trace):
        async with assembly.open(http, trace) as service:
            if tweak:
                tweak(service)
            yield service

    app = create_app(
        Settings(mode="test", request_timeout=60, max_attempts=40),
        trace=sink,
        identity_provider=LocalAuth(path),
        conversation_factory=factory,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://local",
            headers={"Authorization": "Bearer " + TOKEN},
        ) as api,
    ):
        yield api, repo, judge, sink, assembly


async def view(api, run_id, name, session="s", **params):
    return await api.get(f"/debug/runs/{run_id}/{name}", params={"session_id": session, **params})


async def test_three_views_same_trace_facts_tools_versions_and_replay(tmp_path):
    async with client(tmp_path) as (api, repo, judge, trace, assembly):
        body = message("订单000031未享受优惠")
        row = (await api.post("/converse", json=body)).json()
        views = [
            (await view(api, row["run_id"], name)).json() for name in ("intent", "turns", "flow")
        ]
        assert {v["trace_id"] for v in views} == {row["trace_id"]}
        assert all(v["run_id"] == row["run_id"] and not v["incomplete"] for v in views)
        intent, turns, flow = [v["data"] for v in views]
        assert intent["intent"]["final_code"] == row["intent_decision"]["final_code"]
        assert intent["text"]["rule_matches"] and "000031" not in json.dumps(views)
        assert turns["event"]["persisted"] and turns["question"]["version"] == 2
        assert flow["tools"][0]["call_id"] in row["tool_call_ids"]
        assert (
            flow["tools"][0]["result"]["evidence_refs"][0]["record_id"]
            == flow["tools"][0]["call_id"]
        )
        assert flow["stages"][9]["started_at"] <= flow["stages"][9]["finished_at"]
        assert flow["response"]["reply_presentation"]["template"] == flow["response"]["reply"]
        assert flow["sop"]["version"] == row["versions"]["sop"]
        before = len([e for e in trace.events if e.debug])
        replay = (await api.post("/converse", json=body)).json()
        assert replay["replayed"] and replay["call_counts"]["model_calls"] == 0
        assert replay["trace_id"] != row["trace_id"]
        assert (await view(api, replay["run_id"], "flow")).json()["trace_id"] == row["trace_id"]
        assert len([e for e in trace.events if e.debug]) == before
        assert repo.questions[row["question_id"]].status == row["question_status"]


async def test_debug_auth_user_tenant_session_and_export(tmp_path):
    async with client(tmp_path) as (api, repo, judge, trace, assembly):
        row = (await api.post("/converse", json=message("=1+1 我的订单未享受优惠"))).json()
        url = f"/debug/runs/{row['run_id']}/intent"
        assert (
            await api.get(url, params={"session_id": "s"}, headers={"Authorization": ""})
        ).status_code == 401
        for token in ("b" * 32, "c" * 32):
            assert (
                await api.get(
                    url, params={"session_id": "s"}, headers={"Authorization": "Bearer " + token}
                )
            ).status_code == 404
        assert (await view(api, row["run_id"], "intent", "foreign")).status_code == 404
        export = await view(api, row["run_id"], "intent", export="true")
        assert export.status_code == 200 and export.headers["cache-control"] == "no-store"
        assert "attachment" in export.headers["content-disposition"]
        assert export.json()["data"]["input"]["raw_text"].startswith("'=1+1")


async def test_multiturn_correction_and_excluded_history_visible(tmp_path):
    async with client(tmp_path) as (api, repo, judge, trace, assembly):
        first = (await api.post("/converse", json=message("订单000053券不能用"))).json()
        judge.target, judge.relation = first["question_id"], "correction"
        corrected = (await api.post("/converse", json=message("更正：订单000042"))).json()
        turns = (await view(api, corrected["run_id"], "turns")).json()["data"]
        assert turns["question"]["conflicts"] and not corrected["tool_call_ids"]
        conflict = turns["question"]["conflicts"][-1]
        assert conflict["previous"]["value"] != conflict["replacement"]["value"]
        assert (
            conflict["previous"]["source"]["message_id"]
            != conflict["replacement"]["source"]["message_id"]
        )
        assert turns["memory"]["generation"] >= 1 and turns["event"]["edges"]


async def test_bad_intent_is_locatable_from_input_decision_and_versions(tmp_path):
    def tweak(service):
        async def wrong(*args, **kwargs):
            return IntentDecision(
                decision="clarify",
                final_code="COUPON_UNUSABLE",
                candidates=(
                    IntentCandidate(
                        code="COUPON_UNUSABLE", rank=1, score=1, score_kind="model_choice"
                    ),
                ),
                reason_code="synthetic_wrong_intent",
            )

        service.intent.recognize = wrong

    async with client(tmp_path, tweak=tweak) as (api, repo, judge, trace, assembly):
        row = (await api.post("/converse", json=message("我的订单未享受优惠"))).json()
        data = (await view(api, row["run_id"], "intent")).json()["data"]
        assert "未享受优惠" in data["input"]["raw_text"]
        assert data["intent"]["reason_code"] == "synthetic_wrong_intent"
        assert data["intent"]["final_code"] == "COUPON_UNUSABLE" and data["versions"]
        assert not row["tool_call_ids"]


async def test_bad_event_target_is_rejected_and_trace_explains_it(tmp_path):
    async with client(tmp_path) as (api, repo, judge, trace, assembly):
        first = (await api.post("/converse", json=message("订单000053券不能用"))).json()
        judge.target, judge.relation = first["question_id"], "same_complaint"
        row = (await api.post("/converse", json=message("订单000042券还是不能用"))).json()
        data = (await view(api, row["run_id"], "turns")).json()["data"]
        assert row["question_id"] != first["question_id"]
        assert any(c["excluded_reason"] for c in data["event"]["candidates"])
        assert (
            data["event"]["candidates"][0]["question_id"] == first["question_id"]
            and data["versions"]
        )


async def test_actual_stdio_tool_timeout_stage_parameters_and_call_id(tmp_path):
    def tweak(service):
        code = next(c for c in service.sop.definitions if c.value == "DISCOUNT_MISSING")
        definition = service.sop.definitions[code]
        service.sop.definitions[code] = definition.model_copy(
            update={
                "limits": definition.limits.model_copy(
                    update={"tool_seconds": 0.05, "total_retries": 0}
                )
            }
        )
        service.definitions = dict(service.sop.definitions)

    async with client(
        tmp_path, tweak=tweak, mock=MockConfig(faults={"000031": FaultSpec(delay_seconds=0.2)})
    ) as (api, repo, judge, trace, assembly):
        row = (await api.post("/converse", json=message("订单000031未享受优惠"))).json()
        data = (await view(api, row["run_id"], "flow")).json()["data"]
        assert row["outcome"] == "ERROR" and not row["facts"]
        assert data["stages"][9]["status"] == "failed"
        assert data["tools"][0]["result"]["error"]["code"] == "TIMEOUT"
        assert data["tools"][0]["call_id"] in row["tool_call_ids"]
        assert data["tools"][0]["parameters"]["order_id"].startswith("id_")


async def test_trace_disk_failure_does_not_reverse_committed_response(tmp_path, monkeypatch):
    trace = JsonlTrace(tmp_path / "trace.jsonl")
    monkeypatch.setattr(trace, "_append", lambda line: (_ for _ in ()).throw(OSError("full")))
    async with client(tmp_path, trace=trace) as (api, repo, judge, sink, assembly):
        row = (await api.post("/converse", json=message("订单000031未享受优惠"))).json()
        assert row["outcome"] == "ANSWERED"
        assert repo.questions[row["question_id"]].status == "RESOLVED"
        debug = (await view(api, row["run_id"], "flow")).json()
        assert debug["dropped_events"] > 0


async def test_html_three_accessible_views_safe_text_and_no_token_storage(tmp_path):
    async with client(tmp_path) as (api, repo, judge, sink, assembly):
        page = (await api.get("/")).text
        assert all(f'data-view="{v}"' in page for v in ("intent", "turns", "flow"))
        assert "textContent" in page and "innerHTML" not in page
        assert "localStorage" not in page and "sessionStorage" not in page


async def test_expired_diagnostics_do_not_remove_ledger_or_reexecute(tmp_path):
    trace = JsonlTrace(tmp_path / "trace.jsonl")
    async with client(tmp_path, trace=trace) as (api, repo, judge, sink, assembly):
        body = message("订单000031未享受优惠")
        row = (await api.post("/converse", json=body)).json()
        trace.retention_seconds = 0.001
        await asyncio.sleep(0.01)
        assert (await view(api, row["run_id"], "flow")).status_code == 404
        replay = (await api.post("/converse", json=body)).json()
        assert replay["reply"] == row["reply"] and replay["call_counts"]["tool_calls"] == 0
        assert repo.questions[row["question_id"]].status == "RESOLVED"

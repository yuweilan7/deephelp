import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from deephelp_app.adapters.asset_integrity import provenance
from deephelp_app.adapters.history import MySQLHistory
from deephelp_app.adapters.trace import MemoryTrace
from deephelp_app.api.app import create_app
from deephelp_app.api.workbench import COOKIE, BrowserAuth, workbench_router
from deephelp_app.bootstrap.mvp_runtime import LocalAuth
from deephelp_app.bootstrap.settings import Settings
from deephelp_app.domain.models import (
    Question,
    RequestEnvelope,
    ResponseEnvelope,
    RunStatus,
    VerifiedIdentity,
)

pytestmark = pytest.mark.integration
TOKEN = "a" * 32
OWNER = VerifiedIdentity(tenant_id="t", user_id="u")


@asynccontextmanager
async def client(tmp_path, *, ttl=28800):
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps(
            {
                "tokens": [
                    {"token": TOKEN, "identity": OWNER.model_dump()},
                    {"token": "b" * 32, "identity": {"tenant_id": "t", "user_id": "other"}},
                ]
            }
        )
    )
    auth = BrowserAuth(LocalAuth(path), ttl=ttl)
    app = create_app(Settings(mode="test"), trace=MemoryTrace(), identity_provider=auth)
    app.include_router(workbench_router(auth, lambda: {"mode": "offline"}))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as api:
            yield api, auth


async def test_browser_session_csrf_logout_and_bearer_compatibility(tmp_path):
    async with client(tmp_path) as (api, auth):
        assert (await api.get("/auth/session")).status_code == 401
        assert (await api.post("/auth/login", json={"token": "z" * 32})).status_code == 401
        result = await api.post("/auth/login", json={"token": TOKEN})
        assert result.status_code == 200
        assert result.json()["identity"] == OWNER.model_dump()
        cookie = result.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie and TOKEN not in cookie
        opaque = api.cookies.get(COOKIE)
        csrf = result.json()["csrf"]
        assert (await api.get("/auth/session")).json()["csrf"] == csrf
        assert (await api.post("/auth/logout")).status_code == 403
        assert (
            await api.post("/auth/logout", headers={"X-CSRF-Token": "wrong"})
        ).status_code == 403
        assert (
            await api.get("/memory?session_id=x", headers={"Authorization": "Bearer wrong"})
        ).status_code == 401
        assert (
            await api.get("/memory?session_id=x", headers={"Authorization": "Bearer " + TOKEN})
        ).status_code == 501
        assert (await api.post("/auth/logout", headers={"X-CSRF-Token": csrf})).status_code == 200
        api.cookies.set(COOKIE, opaque)
        assert (await api.get("/auth/session")).status_code == 401
        assert not auth.sessions


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Host": "evil.example"},
    ],
)
async def test_cross_site_login_rejected(tmp_path, headers):
    async with client(tmp_path) as (api, _):
        assert (
            await api.post("/auth/login", json={"token": TOKEN}, headers=headers)
        ).status_code == 403


async def test_expiry_and_login_body_cannot_inject_identity(tmp_path):
    async with client(tmp_path, ttl=-1) as (api, _):
        assert (
            await api.post("/auth/login", json={"token": TOKEN, "identity": {"user_id": "other"}})
        ).status_code == 422
        assert (await api.post("/auth/login", json={"token": TOKEN})).status_code == 200
        assert (await api.get("/auth/session")).status_code == 401


async def test_static_assets_and_frontend_release_binding(tmp_path):
    async with client(tmp_path) as (api, _):
        page = await api.get("/")
        assert "学习工作台" in page.text
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        script = await api.get("/workbench.js")
        assert "textContent" in script.text and "innerHTML" not in script.text
        assert "localStorage" not in script.text and "sessionStorage" not in script.text
        assert (await api.get("/workbench.css")).status_code == 200
        assert (await api.get("/debug")).status_code == 200
    hashes = provenance()["artifact_hashes"]
    assert all(
        "frontend/" + name in hashes for name in ("index.html", "workbench.js", "workbench.css")
    )


class Cursor:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    async def execute(self, sql, params):
        self.calls.append((sql, params))

    async def fetchall(self):
        return self.rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class Connection(Cursor):
    def __init__(self, rows):
        self.cursor_value, self.rollbacks = Cursor(rows), 0

    def cursor(self):
        return self.cursor_value

    async def rollback(self):
        self.rollbacks += 1


async def test_mysql_history_scope_pagination_and_original_receipt():
    request = RequestEnvelope(
        identity=OWNER,
        channel="browser",
        session_id="s",
        message_id="m",
        raw_text="订单 000031 未享受优惠",
        occurred_at=datetime.now(UTC),
        received_at=datetime.now(UTC),
        request_id="req",
        trace_id="trace",
    )
    question = Question(
        question_id="q",
        identity=OWNER,
        session_id="s",
        status="WAITING_SLOT",
        version=2,
        member_message_ids=("m",),
        versions={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    response = ResponseEnvelope(
        request_id="req",
        trace_id="trace",
        run_id="run",
        question_id="q",
        outcome="CLARIFY",
        reply="请补充订单号",
    )
    conn = Connection(
        [
            (
                request.model_dump_json(),
                "run",
                RunStatus.SUCCEEDED,
                question.model_dump_json(),
                response.model_dump_json(),
            )
        ]
        * 2
    )
    reader = MySQLHistory(SimpleNamespace(acquire=lambda: conn))
    page = await reader.messages(OWNER, "s", 3, 1)
    assert page.next_offset == 4 and len(page.items) == 1
    assert page.items[0].response == response and page.items[0].input.message_id == "m"
    sql, params = conn.cursor_value.calls[0]
    assert "m.tenant_id=%s AND m.user_id=%s AND q.tenant_id=%s AND q.user_id=%s" in sql
    assert params == ("t", "u", "t", "u", "s", 2, 3)
    assert conn.rollbacks == 1
    conn.cursor_value.rows = [("s", 2, datetime.now()), ("s2", 1, datetime.now())]
    sessions = await reader.sessions(OWNER, 0, 1)
    assert sessions.next_offset == 1 and sessions.items[0].updated_at.tzinfo is not None
    assert conn.cursor_value.calls[1][1] == ("t", "u", "t", "u", 2, 0)

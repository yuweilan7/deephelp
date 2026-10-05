import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from deephelp_app.adapters.asset_integrity import provenance
from deephelp_app.adapters.history import PostgresHistory
from deephelp_app.adapters.trace import MemoryTrace
from deephelp_app.api.app import create_app
from deephelp_app.api.workbench import COOKIE, BrowserAuth, workbench_router
from deephelp_app.bootstrap.mvp_runtime import LocalAuth
from deephelp_app.bootstrap.settings import Settings
from deephelp_app.domain.models.common import RunStatus, VerifiedIdentity
from deephelp_app.domain.models.conversation import Question, RequestEnvelope
from deephelp_app.domain.models.responses import ResponseEnvelope

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
    auth = BrowserAuth(LocalAuth(path), identity=OWNER, ttl=ttl)
    app = create_app(Settings(mode="test"), trace=MemoryTrace(), identity_provider=auth)
    app.include_router(workbench_router(auth, lambda: {"mode": "offline"}))

    from fastapi import Request

    @app.post("/guard")
    async def guard(request: Request):
        return auth(request).model_dump()

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as api:
            yield api, auth


async def test_personal_session_rebuild_csrf_and_bearer_compatibility(tmp_path):
    async with client(tmp_path) as (api, auth):
        result = await api.get("/auth/session")
        assert result.status_code == 200
        assert result.json()["identity"] == OWNER.model_dump()
        assert result.headers["cache-control"] == "no-store"
        cookie = result.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie and TOKEN not in cookie
        opaque = api.cookies.get(COOKIE)
        csrf = result.json()["csrf"]
        assert (await api.get("/auth/session")).json()["csrf"] == csrf
        for marker in (None, "wrong"):
            headers = {} if marker is None else {"X-CSRF-Token": marker}
            assert (await api.post("/guard", headers=headers)).status_code == 403
        assert (
            await api.post("/guard", headers={"X-CSRF-Token": csrf})
        ).json() == OWNER.model_dump()
        assert (
            await api.get("/memory?session_id=x", headers={"Authorization": "Bearer wrong"})
        ).status_code == 401
        assert (
            await api.get("/memory?session_id=x", headers={"Authorization": "Bearer " + TOKEN})
        ).status_code == 501
        other = await api.post("/guard", headers={"Authorization": "Bearer " + "b" * 32})
        assert other.json()["user_id"] == "other"
        assert (
            await api.get("/auth/session", headers={"Authorization": "Bearer " + "b" * 32})
        ).status_code == 403
        # A process restart loses only the browser sessions. Existing cookies cannot
        # authorize writes, then the handshake recovers the same durable owner.
        auth.sessions.clear()
        assert (await api.post("/guard", headers={"X-CSRF-Token": csrf})).status_code == 401
        rebuilt = await api.get("/auth/session")
        assert rebuilt.json()["identity"] == OWNER.model_dump()
        assert api.cookies.get(COOKIE) != opaque and rebuilt.json()["csrf"] != csrf
        assert (await api.post("/guard", headers={"X-CSRF-Token": csrf})).status_code == 403
        assert (
            await api.post("/guard", headers={"X-CSRF-Token": rebuilt.json()["csrf"]})
        ).status_code == 200
        assert (await api.post("/auth/login", json={"token": TOKEN})).status_code == 404
        assert (await api.post("/auth/logout")).status_code == 404


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "http://127.0.0.1:9000"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Host": "evil.example"},
        {"Origin": "null"},
    ],
)
async def test_cross_site_bootstrap_and_cookie_write_rejected(tmp_path, headers):
    async with client(tmp_path) as (api, auth):
        assert (await api.get("/auth/session", headers=headers)).status_code == 403
        assert not auth.sessions
        csrf = (await api.get("/auth/session")).json()["csrf"]
        assert (
            await api.post("/guard", headers={**headers, "X-CSRF-Token": csrf})
        ).status_code == 403


async def test_expiry_and_untrusted_owner_cannot_select_personal_identity(tmp_path):
    async with client(tmp_path, ttl=-1) as (api, _):
        first = await api.get(
            "/auth/session", params={"user_id": "other"}, headers={"X-User": "other"}
        )
        assert first.json()["identity"] == OWNER.model_dump()
        assert (
            await api.post("/guard", headers={"X-CSRF-Token": first.json()["csrf"]})
        ).status_code == 401
        second = await api.get("/auth/session")
        assert second.json()["csrf"] != first.json()["csrf"]


@pytest.mark.parametrize(
    "selection",
    [
        None,
        {},
        {"tenant_id": "t", "user_id": "missing"},
        {"tenant_id": "t", "user_id": "u", "token": TOKEN},
        [OWNER.model_dump()],
    ],
)
def test_personal_owner_missing_invalid_or_unknown_fails(tmp_path, selection):
    from deephelp_app.domain.errors import ConfigurationError

    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({"tokens": [{"token": TOKEN, "identity": OWNER.model_dump()}]}))
    path = tmp_path / "personal.json"
    if selection is not None:
        path.write_text(json.dumps(selection))
    with pytest.raises(ConfigurationError, match="个人|工作台"):
        LocalAuth(auth_path).personal(path)


def test_personal_owner_explicit_selection_and_ambiguity(tmp_path):
    from deephelp_app.domain.errors import ConfigurationError

    path = tmp_path / "personal.json"
    path.write_text(OWNER.model_dump_json())
    auth_path = tmp_path / "auth.json"
    rows = [
        {"token": "b" * 32, "identity": {"tenant_id": "t", "user_id": "other"}},
        {"token": TOKEN, "identity": OWNER.model_dump()},
    ]
    auth_path.write_text(json.dumps({"tokens": rows}))
    assert LocalAuth(auth_path).personal(path) == OWNER
    rows.append({"token": "c" * 32, "identity": OWNER.model_dump()})
    auth_path.write_text(json.dumps({"tokens": rows}))
    with pytest.raises(ConfigurationError, match="唯一"):
        LocalAuth(auth_path).personal(path)


async def test_static_assets_and_frontend_release_binding(tmp_path):
    async with client(tmp_path) as (api, _):
        page = await api.get("/")
        assert "学习工作台" in page.text
        assert all(
            word not in page.text
            for word in ("login-form", "login-panel", 'type="password"', 'id="logout"')
        )
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        script = await api.get("/workbench.js")
        assert "textContent" in script.text and "innerHTML" not in script.text
        assert "localStorage" not in script.text and "sessionStorage" not in script.text
        assert "/auth/login" not in script.text and "/auth/logout" not in script.text
        assert TOKEN not in script.text and "Authorization" not in script.text
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


async def test_postgresql_history_scope_pagination_and_original_receipt():
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
    reader = PostgresHistory(SimpleNamespace(acquire=lambda: conn))
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

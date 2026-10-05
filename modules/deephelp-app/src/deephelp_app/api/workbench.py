"""固定个人身份下的工作台、静态资源与原历史读取。

BrowserAuth同时检查同源/CSRF与既有身份，不从浏览器正文创建主体。
页面、说明和历史读取只投影原记录，明确执行才进入原业务入口与预算。
"""

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field

from deephelp_app.adapters.persistence.history import PostgresHistory
from deephelp_app.application.debug import scope_hash
from deephelp_app.application.run_explanation import explain_run
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import ErrorCode, VerifiedIdentity

COOKIE = "deephelp_session"


@dataclass(frozen=True)
class BrowserSession:
    identity: VerifiedIdentity
    csrf: str
    expires: float


class BrowserAuth:
    """The server chooses the owner; cookies and CSRF only bind a browser to that owner."""

    def __init__(
        self,
        bearer: Callable[[Request], VerifiedIdentity],
        *,
        identity: VerifiedIdentity | None = None,
        ttl: float = 28800,
    ):
        self.bearer, self.ttl = bearer, ttl
        self.identity = identity
        self.sessions: dict[str, BrowserSession] = {}

    def origin(self, request: Request) -> None:
        # Loopback is the current supported deployment. Host alone must not grant DNS rebinding.
        if request.url.hostname not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            raise AppError(ErrorCode.FORBIDDEN, "工作台仅支持本机访问。", 403)
        if origin := request.headers.get("origin"):
            parsed = urlsplit(origin)
            if (parsed.scheme, parsed.netloc) != (request.url.scheme, request.url.netloc):
                raise AppError(ErrorCode.FORBIDDEN, "跨站请求被拒绝。", 403)
        if request.headers.get("sec-fetch-site", "none") not in {"none", "same-origin"}:
            raise AppError(ErrorCode.FORBIDDEN, "跨站请求被拒绝。", 403)

    def session(self, request: Request) -> BrowserSession:
        key = hashlib.sha256(request.cookies.get(COOKIE, "").encode()).hexdigest()
        session = self.sessions.get(key)
        if session is None or session.expires <= monotonic():
            self.sessions.pop(key, None)
            raise AppError(ErrorCode.UNAUTHENTICATED, "工作区连接需要重新建立。", 401)
        return session

    def __call__(self, request: Request) -> VerifiedIdentity:
        if "authorization" in request.headers:
            return self.bearer(request)
        self.origin(request)
        session = self.session(request)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            token = request.headers.get("x-csrf-token", "")
            if not token.isascii() or not hmac.compare_digest(token, session.csrf):
                raise AppError(ErrorCode.FORBIDDEN, "会话确认标记无效。", 403)
        return session.identity


def asset(name: str) -> Path:
    # Serve the same built bytes in editable and wheel installs. Never expose the
    # development source, lock, or node_modules through the asset route.
    root = Path(__file__).parents[1] / "frontend"
    if not root.is_dir():
        root = Path(__file__).parents[3] / "frontend" / "built"
    target = (root / name).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise HTTPException(404, "构建资源不存在，请按工作台说明构建前端。")
    return target


def static_response(name: str) -> FileResponse:
    return FileResponse(
        asset(name),
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
            "style-src-attr 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'",
        },
    )


def workbench_router(auth: BrowserAuth, execution: Callable[[], dict[str, Any]]) -> APIRouter:
    router = APIRouter()

    def no_store(value: Any) -> JSONResponse:
        return JSONResponse(value, headers={"Cache-Control": "no-store"})

    @router.get("/auth/session")
    async def current(request: Request) -> JSONResponse:
        auth.origin(request)
        identity = auth.identity
        if identity is None:
            raise AppError(ErrorCode.NOT_IMPLEMENTED, "请在服务端配置 --personal-identity。", 503)
        if "authorization" in request.headers and auth.bearer(request) != identity:
            raise AppError(ErrorCode.FORBIDDEN, "此入口只建立已配置的个人工作区。", 403)
        # A refresh reuses the opaque session. Restart/expiry creates a new session for
        # the same configured owner; no business state or cumulative budget is reset.
        try:
            session = auth.session(request)
        except AppError as exc:
            if exc.code != ErrorCode.UNAUTHENTICATED:
                raise
        else:
            return no_store(
                dict(
                    identity=session.identity.model_dump(), csrf=session.csrf, execution=execution()
                )
            )
        auth.sessions = {k: v for k, v in auth.sessions.items() if v.expires > monotonic()}
        if len(auth.sessions) >= 128:
            raise AppError(ErrorCode.BUDGET_EXHAUSTED, "工作区连接已满，请稍后重试。", 429)
        cookie, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        auth.sessions[hashlib.sha256(cookie.encode()).hexdigest()] = BrowserSession(
            identity, csrf, monotonic() + auth.ttl
        )
        response = no_store(dict(identity=identity.model_dump(), csrf=csrf, execution=execution()))
        response.set_cookie(
            COOKIE,
            cookie,
            max_age=int(auth.ttl),
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            path="/",
        )
        return response

    def history(request: Request) -> PostgresHistory:
        service = request.app.state.resources.conversation
        ledger = service.ledger if service else None
        if ledger is None or not hasattr(ledger, "pool"):
            raise AppError(ErrorCode.NOT_IMPLEMENTED, "未配置PostgreSQL持久历史。", 501)
        return PostgresHistory(ledger.pool)

    @router.get("/history/sessions")
    async def sessions(
        request: Request,
        offset: int = Query(0, ge=0, le=1000000),
        limit: int = Query(20, ge=1, le=50),
    ) -> JSONResponse:
        identity = auth(request)
        page = await history(request).sessions(identity, offset, limit)
        return no_store(page.model_dump(mode="json"))

    @router.get("/history/runs/{run_id}/explanation")
    async def explanation(
        request: Request,
        run_id: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S+$")],
        session_id: Annotated[str, Query(min_length=1, max_length=128, pattern=r"^\S+$")],
    ) -> JSONResponse:
        identity = auth(request)
        # Authorize against the durable ledger before reading any optional trace.
        # 原PG主体/session/run归属通过后才读可选诊断；trace ID本身不能授予读取权限。
        row = await history(request).run(identity, session_id, run_id)
        sink = request.app.state.resources.trace
        reader, key = getattr(sink, "read_debug", None), getattr(sink, "debug_key", None)
        snapshot = None
        if reader and isinstance(key, bytes):
            try:
                snapshot = await reader(scope_hash(key, identity, session_id), run_id)
            except Exception:
                # Diagnostics are optional; cancellation still propagates.
                pass
        if snapshot is not None and (
            snapshot.run_id != row.run_id
            or (row.response is not None and snapshot.trace_id != row.response.trace_id)
        ):
            snapshot = None
        return no_store(explain_run(row, snapshot, dropped=getattr(sink, "dropped", 0)))

    @router.get("/history/sessions/{session_id}/messages")
    async def messages(
        request: Request,
        session_id: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S+$")],
        offset: int = Query(0, ge=0, le=1000000),
        limit: int = Query(20, ge=1, le=50),
    ) -> JSONResponse:
        identity = auth(request)
        page = await history(request).messages(identity, session_id, offset, limit)
        return no_store(page.model_dump(mode="json"))

    return router

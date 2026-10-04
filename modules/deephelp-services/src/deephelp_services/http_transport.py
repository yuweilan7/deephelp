"""Shared SDK lifecycle, loopback protection and persistent credential initialization."""

import hmac
import json
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from deephelp_app.adapters.network_mcp import ServiceCredentials
from deephelp_app.adapters.telemetry import ObservabilityMiddleware


def initialize_credentials(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump(
            {"bearer": secrets.token_urlsafe(32), "delegation_key": secrets.token_urlsafe(32)},
            output,
        )


def session_manager(server: Server[Any]) -> StreamableHTTPSessionManager:
    return StreamableHTTPSessionManager(
        server,
        json_response=False,
        stateless=False,
        session_idle_timeout=120,
        security_settings=TransportSecuritySettings(
            allowed_hosts=["127.0.0.1:*", "localhost:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        ),
        max_request_body_size=65536,
    )


def protect_and_mount(app: FastAPI, credentials: ServiceCredentials) -> None:
    @app.middleware("http")
    async def service_guard(request: Request, call_next: Any) -> Any:
        token = request.headers.get("authorization", "")
        expected = "Bearer " + credentials.bearer.get_secret_value()
        host = request.headers.get("host", "").split(":")[0]
        origin = request.headers.get("origin")
        if host not in {"127.0.0.1", "localhost"}:
            return JSONResponse({"error": {"code": "FORBIDDEN"}}, status_code=403)
        try:
            invalid_origin = origin and (
                urlsplit(origin).scheme != "http"
                or urlsplit(origin).hostname not in {"127.0.0.1", "localhost"}
            )
        except ValueError:
            invalid_origin = True
        if invalid_origin:
            return JSONResponse({"error": {"code": "FORBIDDEN"}}, status_code=403)
        if not token.isascii() or not hmac.compare_digest(token, expected):
            return JSONResponse({"error": {"code": "UNAUTHENTICATED"}}, status_code=401)
        return await call_next(request)

    class Endpoint:
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            await app.state.mcp_manager.handle_request(scope, receive, send)

    app.add_middleware(ObservabilityMiddleware, accept_parent=True)
    app.router.routes.append(Route("/mcp", endpoint=Endpoint(), methods=["GET", "POST", "DELETE"]))

"""Independent Business MCP and the same owner's authenticated maintenance transactions."""

import argparse
import asyncio
import hashlib
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mcp.server import Server, ServerRequestContext
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    ReadResourceRequestParams,
    ReadResourceResult,
    TextContent,
    TextResourceContents,
)
from pydantic import ValidationError

from deephelp_app.adapters.mcp_protocol import (
    HEALTH_URI,
    TOOL_VERSION,
    canonical,
    registered_tools,
    validate_arguments,
    validate_observation,
)
from deephelp_app.adapters.network_mcp import (
    DelegationVerifier,
    ServiceCredentials,
    decode_delegation,
)
from deephelp_app.adapters.telemetry import environment, server_call
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.models import (
    BusinessInput,
    BusinessRelations,
    BusinessUpdate,
    ErrorCode,
    ToolInvocationEnvelope,
    VerifiedIdentity,
)
from deephelp_services.business_store import BusinessRepository, Kind
from deephelp_services.http_transport import (
    initialize_credentials,
    protect_and_mount,
    session_manager,
)


def make_server(repository: Any, verifier: DelegationVerifier) -> Server[dict[str, Any]]:
    async def listing(
        ctx: ServerRequestContext[dict[str, Any]],
        params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        return ListToolsResult(tools=registered_tools())

    @server_call("mcp.business.call")
    async def calling(
        ctx: ServerRequestContext[dict[str, Any]],
        params: CallToolRequestParams,
    ) -> CallToolResult:
        try:
            payload = verifier.verify(ctx.meta)
            if set(payload) != {"action", "nonce", "envelope"} or payload["action"] != "call_tool":
                raise AppError(ErrorCode.FORBIDDEN, "Trusted tool delegation required")
            envelope = ToolInvocationEnvelope.model_validate(payload["envelope"])
            req = envelope.request
            args = validate_arguments(params.name, params.arguments or {})
            if (
                req.parameters != args
                or req.tool_name.value != params.name
                or payload["nonce"] != envelope.call_id
            ):
                raise AppError(ErrorCode.FORBIDDEN, "Tool delegation differs from arguments")
            if req.tool_version != TOOL_VERSION:
                raise AppError(ErrorCode.VERSION_CONFLICT, "Tool version differs")
            if (
                req.budget.stop_reason
                or req.budget.remaining_attempts == 0
                or req.budget.remaining_seconds <= 0
            ):
                raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Tool budget exhausted")
            async with asyncio.timeout(
                min(req.budget.remaining_seconds, req.timeout_seconds or 30, 30)
            ):
                result = await repository.query(envelope)
            validate_observation(req, result, evidence_version="business-postgres-v1")
            data = result.model_dump(mode="json")
            return CallToolResult(
                content=[TextContent(text=canonical(data).decode())], structured_content=data
            )
        except asyncio.CancelledError:
            raise
        except ValidationError, ValueError, TypeError:
            error = AppError(ErrorCode.INVALID_ARGUMENT, "Invalid tool execution context")
        except TimeoutError:
            error = AppError(ErrorCode.TIMEOUT, "Business query timed out")
        except AppError as exc:
            error = exc
        data = {
            "error": {
                "code": error.code.value,
                "message": error.safe_message,
                "retryable": error.retryable,
            }
        }
        return CallToolResult(
            is_error=True, content=[TextContent(text=error.safe_message)], structured_content=data
        )

    async def reading(
        ctx: ServerRequestContext[dict[str, Any]],
        params: ReadResourceRequestParams,
    ) -> ReadResourceResult:
        payload = verifier.verify(ctx.meta)
        if (
            set(payload) != {"action", "nonce", "uri"}
            or payload["action"] != "read_resource"
            or payload["uri"] != HEALTH_URI
            or str(params.uri) != HEALTH_URI
        ):
            raise AppError(ErrorCode.FORBIDDEN, "Trusted health delegation required")
        return ReadResourceResult(
            contents=[
                TextResourceContents(
                    uri=HEALTH_URI,
                    mime_type="application/json",
                    text=canonical({"pid": os.getpid(), "source": "business-postgres-v1"}).decode(),
                )
            ]
        )

    return Server(
        "deephelp-business",
        version="m20-business-v1",
        on_list_tools=listing,
        on_call_tool=calling,
        on_read_resource=reading,
    )


def create_business_app(
    root: Path, credentials: ServiceCredentials, *, repository: Any = None
) -> FastAPI:
    verifier = DelegationVerifier(credentials, "business")
    app = FastAPI(title="DeepHelp Business", version="m20-business-v1")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        with environment("deephelp-business-mcp"):
            store = repository or await BusinessRepository.open(root)
            app.state.repository = store
            manager = session_manager(make_server(store, verifier))
            app.state.mcp_manager = manager
            try:
                async with manager.run():
                    yield
            finally:
                if repository is None:
                    await store.aclose()

    app.router.lifespan_context = lifespan

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": exc.code.value,
                    "message": exc.safe_message,
                    "retryable": exc.retryable,
                }
            },
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": "INVALID_ARGUMENT", "message": "Invalid business fields"}},
            status_code=422,
        )

    async def identity(request: Request, body: Any = None) -> VerifiedIdentity:
        payload = verifier.verify(
            decode_delegation(request.headers.get("x-deephelp-delegation", ""))
        )
        target = request.url.path + ("?" + request.url.query if request.url.query else "")
        if (
            set(payload) != {"action", "nonce", "identity", "method", "target", "body_hash"}
            or payload["action"] != "maintain"
            or payload["method"] != request.method
            or payload["target"] != target
            or payload["body_hash"] != hashlib.sha256(canonical(body)).hexdigest()
        ):
            raise AppError(ErrorCode.FORBIDDEN, "Maintenance delegation differs from request")
        try:
            return VerifiedIdentity.model_validate(payload["identity"])
        except ValidationError:
            raise AppError(ErrorCode.FORBIDDEN, "Invalid delegated identity") from None

    @app.get("/records/evidence/{evidence_id}")
    async def evidence(evidence_id: str, request: Request) -> Any:
        return await request.app.state.repository.evidence(await identity(request), evidence_id)

    @app.get("/records/{kind}")
    async def listing(
        kind: Kind,
        request: Request,
        offset: int = Query(0, ge=0, le=1000000),
        limit: int = Query(20, ge=1, le=100),
    ) -> Any:
        return await request.app.state.repository.listing(
            kind, await identity(request), offset, limit
        )

    @app.post("/records/{kind}")
    async def creating(kind: Kind, body: BusinessInput, request: Request) -> Any:
        return await request.app.state.repository.put(
            kind, await identity(request, await request.json()), body
        )

    @app.get("/records/orders/{record_id}/activities")
    async def get_relations(record_id: str, request: Request) -> Any:
        return await request.app.state.repository.relations(await identity(request), record_id)

    @app.put("/records/orders/{record_id}/activities")
    async def put_relations(record_id: str, body: BusinessRelations, request: Request) -> Any:
        return await request.app.state.repository.relations(
            await identity(request, await request.json()), record_id, body
        )

    @app.get("/records/{kind}/{record_id}")
    async def getting(kind: Kind, record_id: str, request: Request) -> Any:
        return await request.app.state.repository.get(kind, await identity(request), record_id)

    @app.put("/records/{kind}/{record_id}")
    async def updating(kind: Kind, record_id: str, body: BusinessUpdate, request: Request) -> Any:
        from deephelp_services.business_store import IDS

        if getattr(body.data, IDS[kind], None) != record_id:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Path and business ID differ")
        return await request.app.state.repository.put(
            kind, await identity(request, await request.json()), body.data, body.expected_version
        )

    protect_and_mount(app, credentials)
    return app


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Independent PostgreSQL Business MCP and maintenance"
    )
    parser.add_argument("command", choices=["init", "migrate", "serve"])
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()
    if args.command == "init":
        initialize_credentials(args.credentials)
        print("Service credentials initialized; values are only in the credentials file")
        return 0
    credentials = ServiceCredentials.load(args.credentials)
    if args.command == "serve":
        uvicorn.run(
            create_business_app(args.root, credentials),
            host="127.0.0.1",
            port=args.port,
            access_log=False,
        )
    else:

        async def migrate() -> None:
            store = await BusinessRepository.open(args.root)
            try:
                await store.migrate()
            finally:
                await store.aclose()

        asyncio.run(migrate())
        print("M20 business tables ready; no demo records imported")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AppError, ConfigurationError:
        print("Business service configuration/database failure")
        raise SystemExit(1) from None

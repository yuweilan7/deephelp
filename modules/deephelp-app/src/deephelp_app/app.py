import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from deephelp_app.approval import ApprovalService
from deephelp_app.conversation import Conversation
from deephelp_app.debug import DebugView, export_json, project, sanitize, scope_hash
from deephelp_app.domain.models import (
    ApprovalCommand,
    BudgetUsed,
    ConverseInput,
    DebugSnapshot,
    ErrorCode,
    ErrorDetail,
    LifecycleCommand,
    MemoryWindow,
    NextAction,
    OperationRecord,
    Outcome,
    Question,
    RequestEnvelope,
    ResponseEnvelope,
    ResumeCommand,
    VerifiedIdentity,
)
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.execution import AsyncCalls, ExecutionBudget
from deephelp_app.ports import ModelGateway, Repository
from deephelp_app.settings import Settings
from deephelp_app.trace import JsonlTrace, TraceEvent, TraceSink, request_context


class OfflineTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise ConfigurationError("Network disabled for M01 fake/dev/test client")


@dataclass
class Resources:
    client: httpx.AsyncClient
    calls: AsyncCalls
    gateway: ModelGateway | None
    repository: Repository | None
    trace: TraceSink
    conversation: Conversation | None = None


def local_test_identity(request: Request) -> VerifiedIdentity:
    """Explicit synthetic identity provider, not production authentication."""
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise AppError(ErrorCode.UNAUTHENTICATED, "M01 requires a local test identity", 401)
    return VerifiedIdentity(tenant_id="local-test-tenant", user_id="local-test-user")


def error_response(
    request_id: str, trace_id: str, error: AppError, budget_used: BudgetUsed
) -> JSONResponse:
    outcome = Outcome.ERROR
    next_action = None
    if error.code in {ErrorCode.UNAUTHENTICATED, ErrorCode.FORBIDDEN}:
        outcome = Outcome.REJECTED
    elif error.code == ErrorCode.MISSING_SLOT:
        outcome, next_action = Outcome.CLARIFY, NextAction.PROVIDE_SLOTS
    elif error.code in {ErrorCode.UNKNOWN_INTENT, ErrorCode.NO_SOP}:
        outcome, next_action = Outcome.HANDOFF, NextAction.CONTACT_SUPPORT
    envelope = ResponseEnvelope(
        request_id=request_id,
        trace_id=trace_id,
        outcome=outcome,
        next_action=next_action,
        reply=error.safe_message,
        error=ErrorDetail(code=error.code, message=error.safe_message, retryable=error.retryable),
        budget_used=budget_used,
    )
    return JSONResponse(envelope.model_dump(mode="json"), status_code=error.status_code)


def diagnostic_id(headers: dict[bytes, bytes], name: bytes) -> str:
    # Diagnostic headers never authorize a user/run/operation. Invalid input gets a new ID.
    try:
        value = headers.get(name, b"").decode("ascii")
        return str(UUID(value))
    except ValueError, UnicodeDecodeError:
        return str(uuid4())


class RequestMiddleware:
    """Pure ASGI keeps context and cancellation in the calling task."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        request_id = diagnostic_id(headers, b"x-request-id")
        trace_id = diagnostic_id(headers, b"x-trace-id")
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        state["trace_id"] = trace_id
        budget = ExecutionBudget.start(
            self.settings.request_timeout, self.settings.max_attempts, self.settings.retry_limit
        )
        state["budget"] = budget
        resources: Resources = scope["app"].state.resources
        token = request_context.set((request_id, trace_id))
        started = False
        status_code = 500

        async def traced_send(message: Message) -> None:
            nonlocal started, status_code
            if message["type"] == "http.response.start":
                started = True
                status_code = message["status"]
                message = dict(message)
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() not in {b"x-request-id", b"x-trace-id"}
                ] + [
                    (b"x-request-id", request_id.encode("ascii")),
                    (b"x-trace-id", trace_id.encode("ascii")),
                ]
            await send(message)

        async def record(event: str, error_code: str | None = None) -> None:
            await resources.trace.emit(
                TraceEvent.model_validate(
                    dict(
                        event=event,
                        request_id=request_id,
                        trace_id=trace_id,
                        status_code=status_code if started else None,
                        error_code=error_code,
                    )
                )
            )

        try:
            await record("request_received")
            try:
                if resources.conversation is not None and scope["path"] == "/converse":
                    factory = getattr(scope["app"].state, "budget_factory", None)
                    if factory is not None:
                        budget = factory()
                        state["budget"] = budget
                # The conversation owns its deadline and persists a terminal receipt before return.
                async with asyncio.timeout_at(
                    budget.deadline + (6 if resources.conversation is not None else 0)
                ):
                    await self.app(scope, receive, traced_send)
            except asyncio.CancelledError:
                await record("request_cancelled")
                raise
            except (AppError, TimeoutError) as exc:
                if started:
                    raise
                error = (
                    exc
                    if isinstance(exc, AppError)
                    else AppError(ErrorCode.BUDGET_EXHAUSTED, "Request deadline exhausted", 504)
                )
                await error_response(request_id, trace_id, error, budget.usage())(
                    scope, receive, traced_send
                )
                await record("request_failed", error.code)
            except Exception:
                if started:
                    raise
                # Unexpected failures become explicit HTTP 500, never a synthetic success.
                error = AppError(ErrorCode.INTERNAL_ERROR, "Internal request failure", 500)
                await error_response(request_id, trace_id, error, budget.usage())(
                    scope, receive, traced_send
                )
                await record("request_failed", error.code)
            else:
                await record("request_finished")
        finally:
            request_context.reset(token)


def create_app(
    settings: Settings | None = None,
    *,
    trace: TraceSink | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    identity_provider: Callable[[Request], VerifiedIdentity] = local_test_identity,
    conversation_factory: Callable[
        [httpx.AsyncClient, TraceSink], AbstractAsyncContextManager[Conversation]
    ]
    | None = None,
    budget_factory: Callable[[], ExecutionBudget] | None = None,
) -> FastAPI:
    config = Settings.from_env() if settings is None else settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config.validate_live()
        if config.mode == "live" and conversation_factory is None:
            raise ConfigurationError(
                "Legacy live mode is NOT_IMPLEMENTED; use python -m deephelp_app serve"
            )
        if config.mode == "mvp" and conversation_factory is None:
            raise ConfigurationError("MVP requires explicit adapters and authenticated identity")
        if config.mode == "mvp" and identity_provider is local_test_identity:
            raise ConfigurationError("MVP requires an explicit authenticated identity mapping")
        async with AsyncExitStack() as stack:
            sink = JsonlTrace(Path(config.trace_path)) if trace is None else trace
            stack.push_async_callback(sink.aclose)
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=(
                        httpx.AsyncHTTPTransport(retries=0)
                        if config.mode == "mvp"
                        else OfflineTransport()
                    )
                    if transport is None
                    else transport,
                    limits=httpx.Limits(
                        max_connections=config.http_connections,
                        max_keepalive_connections=config.http_connections,
                    ),
                    timeout=httpx.Timeout(config.child_timeout),
                    trust_env=False,
                    follow_redirects=False,
                )
            )
            calls = AsyncCalls(config.llm_concurrency, config.child_timeout)
            gateway: ModelGateway | None = None
            repository: Repository | None = None
            if conversation_factory is None:
                from deephelp_app.learning.fakes import FakeModelGateway, FakeRepository

                gateway, repository = FakeModelGateway(calls), FakeRepository()
            app.state.resources = Resources(client, calls, gateway, repository, sink)
            if conversation_factory is not None:
                app.state.resources.conversation = await stack.enter_async_context(
                    conversation_factory(client, sink)
                )
            app.state.budget_factory = budget_factory
            yield

    app = FastAPI(title="DeepHelp", version="0.1.0", lifespan=lifespan)
    app.add_middleware(RequestMiddleware, settings=config)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Validation errors contain original input; never serialize/log the full error object.
        return error_response(
            request.state.request_id,
            request.state.trace_id,
            AppError(ErrorCode.INVALID_ARGUMENT, "Invalid request fields", 422),
            request.state.budget.usage(),
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return error_response(
            request.state.request_id,
            request.state.trace_id,
            AppError(ErrorCode.INVALID_ARGUMENT, "HTTP request rejected", exc.status_code),
            request.state.budget.usage(),
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        resources: Resources = app.state.resources
        cases = resources.conversation is not None and resources.conversation.cases is not None
        events = resources.conversation is not None and resources.conversation.events is not None
        return {
            "status": "ok",
            "module": "M12"
            if events
            else "M10"
            if cases
            else "M08"
            if conversation_factory
            else "M01",
            "capability": "READ_ONLY_EVENT_CASCADE"
            if events
            else "READ_ONLY_MULTI_QUESTION"
            if cases
            else "READ_ONLY_SINGLE_MESSAGE"
            if conversation_factory
            else "NOT_IMPLEMENTED",
        }

    @app.get("/", response_class=HTMLResponse)
    async def debug_page() -> str:
        from importlib.resources import files

        return files("deephelp_app").joinpath("debug.html").read_text(encoding="utf-8")

    @app.post(
        "/converse",
        response_model=ResponseEnvelope,
        status_code=200 if conversation_factory else 501,
    )
    async def converse(body: ConverseInput, request: Request) -> JSONResponse:
        identity = identity_provider(request)
        envelope = RequestEnvelope(
            **body.model_dump(),
            identity=identity,
            request_id=request.state.request_id,
            trace_id=request.state.trace_id,
            received_at=datetime.now(UTC),
        )
        resources: Resources = request.app.state.resources
        await resources.trace.emit(
            TraceEvent(
                event="input_validated",
                request_id=envelope.request_id,
                trace_id=envelope.trace_id,
                input_length=len(envelope.raw_text),
            )
        )
        if resources.conversation is not None:
            result = await resources.conversation.run(envelope, request.state.budget)
            return JSONResponse(result.model_dump(mode="json"))
        result = ResponseEnvelope(
            request_id=envelope.request_id,
            trace_id=envelope.trace_id,
            outcome=Outcome.ERROR,
            reply="M01 only provides the asynchronous skeleton; converse is NOT_IMPLEMENTED",
            error=ErrorDetail(
                code=ErrorCode.NOT_IMPLEMENTED, message="Converse is NOT_IMPLEMENTED"
            ),
            budget_used=request.state.budget.usage(),
        )
        return JSONResponse(result.model_dump(mode="json"), status_code=501)

    @app.get("/memory", response_model=MemoryWindow)
    async def memory_window(
        request: Request,
        session_id: Annotated[str, Query(min_length=1, max_length=128, pattern=r"^\S+$")],
    ) -> MemoryWindow:
        identity = identity_provider(request)
        resources: Resources = request.app.state.resources
        service = resources.conversation
        if service is None or service.memory is None:
            raise AppError(ErrorCode.NOT_IMPLEMENTED, "Memory is not configured", 501)
        return await service.memory.load(identity, session_id, request.state.budget)

    @app.post("/questions/{question_id}/state", response_model=Question)
    async def change_question(
        question_id: str, body: LifecycleCommand, request: Request
    ) -> Question:
        identity = identity_provider(request)
        resources: Resources = request.app.state.resources
        service = resources.conversation
        if service is None or service.cases is None:
            raise AppError(ErrorCode.NOT_IMPLEMENTED, "Case repository is not configured", 501)
        return await service.cases.transition(identity, question_id, body)

    def approval_service(request: Request) -> ApprovalService:
        resources: Resources = request.app.state.resources
        service = resources.conversation
        if service is None or service.approvals is None:
            raise AppError(ErrorCode.NOT_IMPLEMENTED, "Durable approval is not configured", 501)
        return service.approvals

    @app.get("/operations/{operation_id}", response_model=OperationRecord)
    async def operation_status(
        operation_id: str, request: Request, session_id: str, run_id: str
    ) -> JSONResponse:
        op = await approval_service(request).repo.scoped(
            identity_provider(request), session_id, run_id, operation_id
        )
        return JSONResponse(op.model_dump(mode="json"), headers={"Cache-Control": "no-store"})

    @app.post("/operations/{operation_id}/approval", response_model=OperationRecord)
    async def approve_operation(
        operation_id: str, body: ApprovalCommand, request: Request
    ) -> OperationRecord:
        return await approval_service(request).decide(
            identity_provider(request), operation_id, body
        )

    @app.post("/operations/{operation_id}/resume", response_model=OperationRecord)
    async def resume_operation(
        operation_id: str, body: ResumeCommand, request: Request
    ) -> OperationRecord:
        return await approval_service(request).resume(
            identity_provider(request), operation_id, body, request.state.budget
        )

    @app.get("/debug/runs/{run_id}/{view}")
    async def debug_view(
        run_id: str,
        view: DebugView,
        request: Request,
        session_id: Annotated[str, Query(min_length=1, max_length=128, pattern=r"^\S+$")],
        export: bool = False,
    ) -> Response:
        identity = identity_provider(request)
        sink = request.app.state.resources.trace
        reader, key = getattr(sink, "read_debug", None), getattr(sink, "debug_key", None)
        if reader is None or not isinstance(key, bytes):
            raise AppError(ErrorCode.NOT_IMPLEMENTED, "Debug trace is not configured", 501)
        if len(run_id) > 128:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Invalid run identifier", 422)
        snapshot = await reader(scope_hash(key, identity, session_id), run_id)
        if snapshot is None:
            # Foreign IDs and expired diagnostics return the same result.
            raise AppError(ErrorCode.INVALID_ARGUMENT, "追踪不可用、已过期或已丢弃。", 404)
        snapshot = DebugSnapshot.model_validate(snapshot)
        service = request.app.state.resources.conversation
        if service is not None and service.approvals is not None:
            op = await service.approvals.repo.by_run(run_id)
            if op is not None:
                await service.approvals.repo.scoped(
                    identity, session_id, run_id, op.plan.operation_id
                )
                approval_view = sanitize(
                    {
                        "operation_id": op.plan.operation_id,
                        "status": op.status.value,
                        "approval_status": op.approval_status.value,
                        "expires_at": op.expires_at.isoformat(),
                        "dispatch_attempts": op.dispatch_attempts,
                        "query_attempts": op.query_attempts,
                        "history": await service.approvals.repo.history(op.plan.operation_id),
                        "response": op.response.model_dump(mode="json") if op.response else None,
                    },
                    key,
                )
                snapshot = snapshot.model_copy(
                    update={
                        "data": {**snapshot.data, "approval": approval_view},
                    }
                )
        value = project(
            DebugSnapshot.model_validate(snapshot), view, dropped=getattr(sink, "dropped", 0)
        )
        if export:
            return Response(
                export_json(value),
                media_type="application/json",
                headers={
                    "Content-Disposition": f'attachment; filename="deephelp-{view}.json"',
                    "Cache-Control": "no-store",
                },
            )
        return JSONResponse(value, headers={"Cache-Control": "no-store"})

    return app

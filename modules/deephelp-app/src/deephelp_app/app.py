import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from deephelp_app.domain.models import (
    BudgetUsed,
    ConverseInput,
    ErrorCode,
    ErrorDetail,
    Outcome,
    RequestEnvelope,
    ResponseEnvelope,
    VerifiedIdentity,
)
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.execution import AsyncCalls, ExecutionBudget
from deephelp_app.fakes import FakeModelGateway, FakeRepository
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
    gateway: ModelGateway
    repository: Repository
    trace: TraceSink


def local_test_identity(request: Request) -> VerifiedIdentity:
    """Explicit synthetic identity provider, not production authentication."""
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise AppError(ErrorCode.UNAUTHENTICATED, "M01 requires a local test identity", 401)
    return VerifiedIdentity(tenant_id="local-test-tenant", user_id="local-test-user")


def error_response(
    request_id: str, trace_id: str, error: AppError, budget_used: BudgetUsed
) -> JSONResponse:
    envelope = ResponseEnvelope(
        request_id=request_id,
        trace_id=trace_id,
        outcome=(
            Outcome.REJECTED
            if error.code in {ErrorCode.UNAUTHENTICATED, ErrorCode.FORBIDDEN}
            else Outcome.ERROR
        ),
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
    except (ValueError, UnicodeDecodeError):
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
                async with asyncio.timeout_at(budget.deadline):
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
) -> FastAPI:
    config = Settings.from_env() if settings is None else settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config.validate_live()
        if config.mode == "live":
            raise ConfigurationError("M01 live ModelGateway is NOT_IMPLEMENTED; see M03")
        async with AsyncExitStack() as stack:
            sink = JsonlTrace(Path(config.trace_path)) if trace is None else trace
            stack.push_async_callback(sink.aclose)
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=OfflineTransport() if transport is None else transport,
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
            app.state.resources = Resources(
                client, calls, FakeModelGateway(calls), FakeRepository(), sink
            )
            yield

    app = FastAPI(title="DeepHelp M01 skeleton", version="0.1.0", lifespan=lifespan)
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
        return {"status": "ok", "module": "M01", "capability": "NOT_IMPLEMENTED"}

    @app.post("/converse", response_model=ResponseEnvelope, status_code=501)
    async def converse(body: ConverseInput, request: Request) -> ResponseEnvelope:
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
        return ResponseEnvelope(
            request_id=envelope.request_id,
            trace_id=envelope.trace_id,
            outcome=Outcome.ERROR,
            reply="M01 only provides the asynchronous skeleton; converse is NOT_IMPLEMENTED",
            error=ErrorDetail(
                code=ErrorCode.NOT_IMPLEMENTED, message="Converse is NOT_IMPLEMENTED"
            ),
            budget_used=request.state.budget.usage(),
        )

    return app

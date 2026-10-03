"""One owned SDK session per lifespan; only verified read-only requests reach MCP."""

import asyncio
import json
import math
import secrets
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, TextIO
from uuid import uuid4

import anyio
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, TextContent, TextResourceContents, Tool
from pydantic import ValidationError

from deephelp_app.demo.tool_config import MockConfig
from deephelp_app.domain.checks import (
    require_owner,
    validate_question_access,
    validate_tool_context,
    validate_tool_result,
)
from deephelp_app.domain.models import (
    ErrorCode,
    ErrorDetail,
    Question,
    RequestEnvelope,
    ToolInvocationEnvelope,
    ToolInvocationRecord,
    ToolRequest,
    ToolResult,
    ToolStatus,
)
from deephelp_app.errors import AppError
from deephelp_app.execution import AsyncCalls, ExecutionBudget
from deephelp_app.mcp_protocol import (
    HEALTH_URI,
    LEDGER_URI,
    TOOL_VERSION,
    canonical,
    registered_tools,
    sign_metadata,
    validate_arguments,
    validate_observation,
)


class ToolGateway:
    def __init__(
        self,
        *,
        config: MockConfig | None = None,
        child_timeout: float = 5,
        concurrency: int = 4,
        max_result_bytes: int = 65536,
        startup_timeout: float = 15,
        ledger_path: Path | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        if (
            max_result_bytes < 1024
            or max_result_bytes > 1048576
            or not math.isfinite(startup_timeout)
            or startup_timeout <= 0
            or not math.isfinite(child_timeout)
        ):
            raise ValueError("Invalid MCP limits")
        self.config = config or MockConfig()
        self.calls = AsyncCalls(concurrency, child_timeout)
        self.max_result_bytes = max_result_bytes
        self.startup_timeout = startup_timeout
        self.ledger_path = ledger_path
        self.stderr = stderr
        self._secret = secrets.token_hex(32)
        self._client: Client | None = None
        self.pid: int | None = None
        self.diagnostics: list[dict[str, Any]] = []
        self._entered = False

    @asynccontextmanager
    async def open(self) -> AsyncIterator[ToolGateway]:
        if self._entered:
            raise ValueError("Gateway lifespan is single-use")
        self._entered = True
        env = {
            "DEEPHELP_MCP_SESSION_SECRET": self._secret,
            "DEEPHELP_MCP_CONFIG": self.config.model_dump_json(),
            "PYTHONUTF8": "1",
        }
        if self.ledger_path is not None:
            env["DEEPHELP_MCP_LEDGER"] = str(self.ledger_path.resolve())
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "deephelp_app.demo.mcp_server"], env=env
        )
        # The SDK owns process cleanup. Enter/exit in the same task for AnyIO scope ordering.
        from mcp.client.stdio import stdio_client

        transport = stdio_client(params, errlog=self.stderr or sys.stderr)
        client = Client(transport, cache=None)
        async with asyncio.timeout(self.startup_timeout):
            await client.__aenter__()
        self._client = client
        try:
            async with asyncio.timeout(self.startup_timeout):
                await self.list_tools()
                health = await self._resource(HEALTH_URI)
                pid = health.get("pid")
                if type(pid) is not int or pid <= 0:
                    raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Invalid MCP health response")
                self.pid = pid
            yield self
        finally:
            self._client = None
            # Close SDK scopes normally so caller errors/cancellation retain their original type.
            await client.__aexit__(None, None, None)

    def _connected(self) -> Client:
        if self._client is None:
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "Tool gateway is not connected")
        return self._client

    async def list_tools(self) -> tuple[Tool, ...]:
        async with asyncio.timeout(self.startup_timeout):
            result = await self._connected().list_tools()
        expected = {t.name: t for t in registered_tools()}
        if (
            result.next_cursor is not None
            or len(result.tools) != len(expected)
            or {t.name for t in result.tools} != set(expected)
        ):
            raise AppError(ErrorCode.MODEL_CAPABILITY_UNAVAILABLE, "MCP registry mismatch")
        for tool in result.tools:
            known = expected[tool.name]
            if (
                tool.input_schema != known.input_schema
                or tool.output_schema != known.output_schema
                or tool.annotations != known.annotations
                or tool.meta != known.meta
                or tool.description != known.description
            ):
                raise AppError(
                    ErrorCode.MODEL_CAPABILITY_UNAVAILABLE, "MCP schema/version mismatch"
                )
        return tuple(result.tools)

    async def _resource(self, uri: str) -> dict[str, Any]:
        # Administrative diagnostics are not tools and must never be exposed to a model.
        meta = sign_metadata(
            self._secret, {"action": "read_resource", "uri": uri, "nonce": uuid4().hex}
        )
        async with asyncio.timeout(self.startup_timeout):
            result = await self._connected().read_resource(uri, meta=meta, cache_mode="bypass")
        if len(result.contents) != 1 or not isinstance(result.contents[0], TextResourceContents):
            raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Invalid diagnostic resource")
        data = json.loads(result.contents[0].text)
        if not isinstance(data, dict):
            raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Invalid diagnostic resource")
        return data

    async def ledger(self) -> tuple[ToolInvocationRecord, ...]:
        data = await self._resource(LEDGER_URI)
        return tuple(ToolInvocationRecord.model_validate(r) for r in data["records"])

    async def execute(
        self,
        request: ToolRequest,
        question: Question,
        budget: ExecutionBudget,
        *,
        context: RequestEnvelope,
        on_dispatch: Callable[[str], None] | None = None,
    ) -> ToolResult:
        """Context is injected by the authenticated entry point, never model tool arguments."""
        call_id = "call-" + uuid4().hex
        dispatched = False
        try:
            require_owner(context.identity, request.identity)
            validate_question_access(context, question)
            validate_tool_context(request, question)
            if request.tool_version != TOOL_VERSION:
                raise AppError(ErrorCode.VERSION_CONFLICT, "Tool version mismatch")
            arguments = request.parameters.model_dump(mode="json", exclude_none=True)
            validate_arguments(request.tool_name.value, arguments)
            self._connected()

            async def operation() -> ToolResult:
                nonlocal dispatched
                # Refresh after queueing. The mutable budget owns the actual deadline.
                snapshot = budget.snapshot().model_copy(
                    update={
                        "stop_reason": None,
                        "remaining_attempts": budget.remaining_attempts + 1,
                    }
                )
                envelope = ToolInvocationEnvelope(
                    request=request.model_copy(update={"budget": snapshot}),
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    call_id=call_id,
                )
                meta = sign_metadata(
                    self._secret,
                    {
                        "action": "call_tool",
                        "nonce": call_id,
                        "envelope": envelope.model_dump(mode="json"),
                    },
                )
                try:
                    dispatched = True
                    budget.tool_steps_used += 1
                    if on_dispatch:
                        on_dispatch(call_id)
                    wire = await self._connected().call_tool(
                        request.tool_name.value, arguments, meta=meta
                    )
                except RuntimeError:
                    # SDK output-schema rejection may contain raw result data; keep a static error.
                    raise AppError(
                        ErrorCode.MODEL_OUTPUT_INVALID, "MCP result failed schema validation"
                    ) from None
                except MCPError, anyio.BrokenResourceError, anyio.ClosedResourceError, OSError:
                    raise AppError(
                        ErrorCode.UPSTREAM_UNAVAILABLE, "MCP transport unavailable", retryable=True
                    ) from None
                return self._parse(request, call_id, wire)

            # No automatic retry: timeout/cancellation has a unique observable invocation.
            result = await self.calls.call(
                operation, budget, retry_safe=False, child_timeout=request.timeout_seconds
            )
            self._record(request, context, result)
            return result
        except asyncio.CancelledError:
            self._record(request, context, None, cancelled=True, call_id=call_id)
            raise
        except AppError as exc:
            result = ToolResult(
                operation_id=request.operation_id,
                run_id=request.run_id,
                tool_name=request.tool_name,
                tool_version=request.tool_version,
                status=ToolStatus.FAILED,
                call_id=call_id if dispatched else None,
                error=ErrorDetail(code=exc.code, message=exc.safe_message, retryable=exc.retryable),
            )
            self._record(request, context, result, call_id=call_id)
            return result

    def _record(
        self,
        request: ToolRequest,
        context: RequestEnvelope,
        result: ToolResult | None,
        *,
        cancelled: bool = False,
        call_id: str | None = None,
    ) -> None:
        if len(self.diagnostics) >= self.config.max_invocations:
            self.diagnostics.pop(0)
        self.diagnostics.append(
            {
                "request_id": context.request_id,
                "trace_id": context.trace_id,
                "operation_id": request.operation_id,
                "tool": request.tool_name.value,
                "call_id": result.call_id if result and result.call_id else call_id,
                "status": "cancelled"
                if cancelled
                else result.status.value
                if result
                else "unknown",
                "error_code": result.error.code.value if result and result.error else None,
                "evidence_ids": [r.evidence_id for r in result.evidence_refs] if result else [],
            }
        )

    def _parse(self, request: ToolRequest, call_id: str, wire: CallToolResult) -> ToolResult:
        try:
            if len(canonical(wire.model_dump(mode="json"))) > self.max_result_bytes:
                raise ValueError
            data = wire.structured_content
            if wire.is_error:
                if not isinstance(data, dict) or set(data) != {"error"}:
                    raise ValueError
                error = ErrorDetail.model_validate(data["error"])
                # Remote text is data, including error messages; publish only a fixed message.
                raise AppError(
                    error.code, "Synthetic tool invocation failed", retryable=error.retryable
                )
            if (
                len(wire.content) != 1
                or not isinstance(wire.content[0], TextContent)
                or json.loads(wire.content[0].text) != data
            ):
                raise ValueError
            result = ToolResult.model_validate(data)
            validate_tool_result(request, result)
            if (
                result.status != ToolStatus.SUCCEEDED
                or result.call_id != call_id
                or result.upstream_request_id != call_id
            ):
                raise ValueError
            validate_observation(request, result)
            return result
        except ValueError, TypeError, ValidationError:
            raise AppError(
                ErrorCode.MODEL_OUTPUT_INVALID, "Invalid or contradictory MCP result"
            ) from None


def process_alive(pid: int) -> bool:
    """Check a known child PID without sending Windows kill/termination signals."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True

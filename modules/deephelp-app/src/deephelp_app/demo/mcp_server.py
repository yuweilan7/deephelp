"""Owned SDK stdio server: synthetic data, real MCP, no business writes or model calls."""

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    ReadResourceRequestParams,
    ReadResourceResult,
    RequestParamsMeta,
    TextContent,
    TextResourceContents,
)
from pydantic import ValidationError

from deephelp_app.demo.fixtures import BusinessFixtures, load_business_fixtures
from deephelp_app.demo.tool_config import FaultSpec, MockConfig
from deephelp_app.domain.checks import require_owner
from deephelp_app.domain.models import (
    DTO,
    ErrorCode,
    EvidenceRef,
    EvidenceSource,
    Fact,
    FactKind,
    ToolInvocationEnvelope,
    ToolInvocationRecord,
    ToolName,
    ToolResult,
    ToolStatus,
)
from deephelp_app.errors import AppError
from deephelp_app.mcp_protocol import (
    HEALTH_URI,
    LEDGER_URI,
    TOOL_VERSION,
    canonical,
    registered_tools,
    validate_arguments,
    verify_metadata,
)


class MockBackend:
    def __init__(
        self,
        secret: str,
        config: MockConfig,
        fixtures: BusinessFixtures | None = None,
        ledger_path: Path | None = None,
    ) -> None:
        self.secret = secret
        self.config = config
        self.fixtures = fixtures or config.fixtures or load_business_fixtures()
        self.records: dict[str, ToolInvocationRecord] = {}
        self.nonces: set[str] = set()
        self.ledger_path = ledger_path
        if ledger_path is not None:
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            if ledger_path.exists():
                raise ValueError("Ledger output must be new")
            self.persist()

    def persist(self) -> None:
        # Small bounded synthetic snapshots only; secret/signature never enter this file.
        if self.ledger_path is not None:
            temporary = self.ledger_path.with_name(
                self.ledger_path.name + "." + uuid4().hex + ".tmp"
            )
            temporary.write_bytes(canonical(self.snapshot()))
            temporary.replace(self.ledger_path)

    def snapshot(self) -> dict[str, Any]:
        return {
            "pid": os.getpid(),
            "records": [r.model_dump(mode="json") for r in self.records.values()],
        }

    def authenticate(self, meta: RequestParamsMeta | None) -> dict[str, Any]:
        payload = verify_metadata(self.secret, meta)
        nonce = payload.get("nonce")
        if not isinstance(nonce, str) or not nonce or len(nonce) > 128 or nonce in self.nonces:
            raise AppError(ErrorCode.FORBIDDEN, "Invalid or replayed execution context")
        if len(self.nonces) >= 8192:
            raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Session metadata budget exhausted")
        self.nonces.add(nonce)
        return payload

    def update(
        self,
        envelope: ToolInvocationEnvelope,
        status: Literal["started", "succeeded", "failed", "cancelled"],
        *,
        code: ErrorCode | None = None,
        evidence: tuple[str, ...] = (),
    ) -> None:
        req = envelope.request
        self.records[envelope.call_id] = ToolInvocationRecord(
            call_id=envelope.call_id,
            operation_id=req.operation_id,
            run_id=req.run_id,
            question_id=req.question_id,
            request_id=envelope.request_id,
            trace_id=envelope.trace_id,
            identity=req.identity,
            tool_name=req.tool_name,
            parameters=req.parameters,
            status=status,
            error_code=code,
            evidence_ids=evidence,
        )
        self.persist()

    async def invoke(
        self, name: str, arguments: object, meta: RequestParamsMeta | None
    ) -> CallToolResult:
        envelope = None
        started = False
        try:
            payload = self.authenticate(meta)
            if set(payload) != {"action", "nonce", "envelope"} or payload["action"] != "call_tool":
                raise AppError(ErrorCode.FORBIDDEN, "Invalid execution context")
            envelope = ToolInvocationEnvelope.model_validate(payload["envelope"])
            req = envelope.request
            parameters = validate_arguments(name, arguments)
            if req.tool_name.value != name or req.parameters != parameters:
                raise AppError(ErrorCode.FORBIDDEN, "Execution context does not match arguments")
            if req.tool_version != TOOL_VERSION:
                raise AppError(ErrorCode.VERSION_CONFLICT, "Tool version mismatch")
            if (
                req.budget.stop_reason is not None
                or req.budget.remaining_attempts == 0
                or req.budget.remaining_seconds <= 0
            ):
                raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Tool budget is exhausted")
            if payload["nonce"] != envelope.call_id or envelope.call_id in self.records:
                raise AppError(ErrorCode.FORBIDDEN, "Invalid or replayed call identity")
            if len(self.records) >= self.config.max_invocations:
                raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Invocation ledger budget exhausted")
            self.update(envelope, "started")
            started = True
            async with asyncio.timeout(min(req.budget.remaining_seconds, 60)):
                result = await self.query(envelope)
            self.update(
                envelope, "succeeded", evidence=tuple(r.evidence_id for r in result.evidence_refs)
            )
            fault = self.fault(req.tool_name, parameters.order_id)
            data = result.model_dump(mode="json")
            if fault.kind != "normal":
                from deephelp_app.learning.mcp_faults import alter_result

                alter_result(data, fault)
            return CallToolResult(
                content=[TextContent(text=json.dumps(data, ensure_ascii=False))],
                structured_content=data,
            )
        except asyncio.CancelledError:
            if envelope is not None and started:
                self.update(envelope, "cancelled")
            raise
        except ValidationError:
            return self.error(ErrorCode.INVALID_ARGUMENT, "Invalid internal execution context")
        except TimeoutError:
            if envelope is not None and started:
                self.update(envelope, "failed", code=ErrorCode.TIMEOUT)
            return self.error(ErrorCode.TIMEOUT, "Synthetic tool timed out", retryable=True)
        except AppError as exc:
            if envelope is not None and started:
                self.update(envelope, "failed", code=exc.code)
            return self.error(exc.code, exc.safe_message, retryable=exc.retryable)

    @staticmethod
    def error(code: ErrorCode, message: str, *, retryable: bool = False) -> CallToolResult:
        data = {"error": {"code": code.value, "message": message, "retryable": retryable}}
        return CallToolResult(
            is_error=True, content=[TextContent(text=message)], structured_content=data
        )

    async def query(self, envelope: ToolInvocationEnvelope) -> ToolResult:
        req = envelope.request
        order = next(
            (o for o in self.fixtures.orders if o.order_id == req.parameters.order_id), None
        )
        if order is None:
            raise AppError(ErrorCode.NOT_FOUND, "Synthetic object not found")
        require_owner(req.identity, order.identity)
        coupon = None
        if req.tool_name == ToolName.CHECK_COUPON:
            coupon = next(
                (c for c in self.fixtures.coupons if c.coupon_id == req.parameters.coupon_id), None
            )
            if coupon is None:
                raise AppError(ErrorCode.NOT_FOUND, "Synthetic object not found")
            require_owner(req.identity, coupon.identity)
            if coupon.order_id != order.order_id:
                raise AppError(ErrorCode.INVALID_ARGUMENT, "Coupon does not belong to order")
        fault = self.fault(req.tool_name, order.order_id)
        if fault.kind != "normal" or fault.delay_seconds:
            from deephelp_app.learning.mcp_faults import before_query

            await before_query(fault)
        evidence_id = "ev-" + uuid4().hex
        values: dict[str, Any] = {"order_id": order.order_id}
        if coupon is None:
            labels = {a.activity_id: a.label for a in self.fixtures.activities}
            values.update(
                paid=order.paid,
                discount=order.discount,
                discount_status=order.discount_status,
                activity_ids=",".join(order.activity_ids),
                activity_labels=",".join(labels[a] for a in order.activity_ids),
            )
        else:
            values.update(
                coupon_id=coupon.coupon_id,
                coupon_status=coupon.status,
                minimum_spend=coupon.minimum_spend,
                usable=coupon.status == "usable",
            )
        facts = tuple(
            Fact(
                name=k,
                kind=(
                    FactKind.FLAG
                    if isinstance(v, bool)
                    else FactKind.TEXT
                    if isinstance(v, str)
                    else FactKind.MONEY
                ),
                value=v,
                evidence_ids=(evidence_id,),
            )
            for k, v in values.items()
        )
        summary = "合成 fixture 查询证据"
        if fault.kind == "injection":
            from deephelp_app.learning.mcp_faults import evidence_summary

            summary = evidence_summary(fault)
        return ToolResult(
            operation_id=req.operation_id,
            run_id=req.run_id,
            tool_name=req.tool_name,
            tool_version=TOOL_VERSION,
            call_id=envelope.call_id,
            upstream_request_id=envelope.call_id,
            status=ToolStatus.SUCCEEDED,
            facts=facts,
            evidence_refs=(
                EvidenceRef(
                    evidence_id=evidence_id,
                    source=EvidenceSource.TOOL,
                    record_id=envelope.call_id,
                    version=self.fixtures.version,
                    locator=order.order_id,
                    content_hash=hashlib.sha256(
                        canonical(
                            {
                                k: (v.model_dump(mode="json") if isinstance(v, DTO) else v)
                                for k, v in values.items()
                            }
                        )
                    ).hexdigest(),
                    summary=summary,
                ),
            ),
        )

    def fault(self, tool: ToolName, order_id: str) -> FaultSpec:
        return self.config.tool_faults.get(
            tool.value + ":" + order_id, self.config.faults.get(order_id, FaultSpec())
        )


def make_server(backend: MockBackend) -> Server[dict[str, Any]]:
    async def listing(
        ctx: ServerRequestContext[dict[str, Any]], params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        return ListToolsResult(tools=registered_tools())

    async def calling(
        ctx: ServerRequestContext[dict[str, Any]], params: CallToolRequestParams
    ) -> CallToolResult:
        return await backend.invoke(params.name, params.arguments or {}, ctx.meta)

    async def reading(
        ctx: ServerRequestContext[dict[str, Any]], params: ReadResourceRequestParams
    ) -> ReadResourceResult:
        payload = backend.authenticate(ctx.meta)
        uri = str(params.uri)
        if payload != {"action": "read_resource", "uri": uri, "nonce": payload["nonce"]}:
            raise AppError(ErrorCode.FORBIDDEN, "Trusted resource context required")
        if uri == LEDGER_URI:
            data = backend.snapshot()
        elif uri == HEALTH_URI:
            data = {
                "pid": os.getpid(),
                "fixture_version": backend.fixtures.version,
                "invocations": len(backend.records),
            }
        else:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Unknown diagnostic resource")
        return ReadResourceResult(
            contents=[
                TextResourceContents(
                    uri=uri, mime_type="application/json", text=canonical(data).decode()
                )
            ]
        )

    return Server(
        "deephelp-m06-synthetic",
        version=TOOL_VERSION,
        on_list_tools=listing,
        on_call_tool=calling,
        on_read_resource=reading,
    )


async def main() -> None:
    secret = os.environ.get("DEEPHELP_MCP_SESSION_SECRET", "")
    if len(secret) < 32:
        raise ValueError("Internal session secret required; launch through ToolGateway")
    config = MockConfig.model_validate_json(os.environ.get("DEEPHELP_MCP_CONFIG", "{}"))
    path = os.environ.get("DEEPHELP_MCP_LEDGER")
    backend = MockBackend(secret, config, ledger_path=Path(path) if path else None)
    server = make_server(backend)
    async with stdio_server() as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ValueError, OSError:
        print("M06 server configuration/storage failure", file=sys.stderr)
        raise SystemExit(1) from None

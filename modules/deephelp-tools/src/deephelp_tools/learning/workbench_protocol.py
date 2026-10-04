"""Fixed protocol experiments and scoped views of the original M15 approval graph."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import uuid4

from fastapi import APIRouter, Request

from deephelp_app.adapters.approval_store import ApprovalRepository
from deephelp_app.adapters.mcp_protocol import TOOL_VERSION, validate_arguments
from deephelp_app.adapters.network_mcp import NetworkMCPClient
from deephelp_app.adapters.postgres import application_config
from deephelp_app.adapters.postgres_checkpoints import official_checkpoints
from deephelp_app.adapters.synthetic_rights import RightsClient
from deephelp_app.api.workbench import BrowserAuth
from deephelp_app.application.approval import ApprovalService
from deephelp_app.bootstrap.mvp_runtime import LiveAssembly
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models import (
    DTO,
    ApprovalCommand,
    Entity,
    EntityName,
    EntitySource,
    ErrorCode,
    Identifier,
    IntentCode,
    Question,
    QuestionStatus,
    RequestEnvelope,
    ResumeCommand,
    ToolInvocationEnvelope,
    ToolName,
    ToolParameters,
    ToolRequest,
    VerifiedIdentity,
    tool_parameters_hash,
)
from deephelp_tools.demo.approval_prepare import prepare
from deephelp_tools.demo.scenarios import proposal_registry

DEMO_IDENTITY = VerifiedIdentity(tenant_id="synthetic-tenant", user_id="synthetic-user-a")


class ProtocolAction(DTO):
    action: Literal["benefits", "coupon", "client_invalid", "server_invalid"]
    order_id: Identifier = "90221004"
    coupon_id: Identifier = "90222004"


def learning_router(
    assembly: LiveAssembly,
    auth: BrowserAuth,
    *,
    rights_port: int | None = None,
    rights_key: bytes | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/learning")
    if rights_port is not None and (not rights_key or len(rights_key) != 32):
        raise ValueError("Learning rights key must contain 32 bytes")

    def ready() -> None:
        if assembly.ledger is None or assembly.tools is None:
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "Learning runtime is not ready", 503)

    @asynccontextmanager
    async def approval(request: Request) -> AsyncIterator[ApprovalService]:
        ready()
        if rights_port is None or rights_key is None:
            raise AppError(ErrorCode.NOT_IMPLEMENTED, "Synthetic demo is not configured", 501)
        assert assembly.ledger is not None
        async with ApprovalService.open(
            ApprovalRepository(assembly.ledger.pool),
            RightsClient(request.app.state.resources.client, rights_port, rights_key),
            proposal_registry().snapshot_hash,
            root=assembly.root,
        ) as service:
            yield service

    @asynccontextmanager
    async def scoped(request: Request, operation_id: str, session_id: str, run_id: str) -> Any:
        async with approval(request) as service:
            op = await service.repo.scoped(auth(request), session_id, run_id, operation_id)
            if not session_id.startswith("m22-demo-") or op.plan.parameters.order_id != "DEMO-D01":
                raise AppError(ErrorCode.NOT_FOUND, "Learning demo operation not found", 404)
            yield service, op

    @router.get("/status")
    async def status(request: Request) -> dict[str, Any]:
        identity = auth(request)
        return {
            "installed": True,
            "protocol": True,
            "workflow": True,
            "analysis": True,
            "demo_enabled": rights_port is not None and identity == DEMO_IDENTITY
            if assembly.ledger is not None
            else False,
            "demo_identity_required": "synthetic-tenant / synthetic-user-a",
            "execution_gate": None,
            "model_calls": 0,
        }

    @router.get("/protocol")
    async def catalog(request: Request) -> dict[str, Any]:
        auth(request)
        ready()
        assert assembly.tools is not None
        tools = await assembly.tools.list_tools()
        client = assembly.tools._connected()
        return {
            "tools": [t.model_dump(mode="json", by_alias=True) for t in tools],
            "initialize": client.handshakes[-1]
            if isinstance(client, NetworkMCPClient)
            else {"initialized": True, "transport": "owned stdio"},
            "source": assembly.tools.evidence_version,
            "server_pid": assembly.tools.pid,
            "trace_id": request.state.trace_id,
            "process": "app → MCP SDK → Business service",
            "code": [
                "adapters/tool_gateway.py:ToolGateway.list_tools",
                "adapters/network_mcp.py:NetworkMCPClient.session",
                "deephelp_services/business.py:build_server",
            ],
            "model_calls": 0,
        }

    @router.post("/protocol")
    async def experiment(body: ProtocolAction, request: Request) -> dict[str, Any]:
        identity = auth(request)
        ready()
        assert assembly.tools is not None
        gateway, budget = assembly.tools, request.state.budget
        result: dict[str, Any] = {
            "action": body.action,
            "model_calls": 0,
            "trace_id": request.state.trace_id,
        }
        if body.action == "client_invalid":
            try:
                validate_arguments("get_order_benefits", {"order_id": 123})
            except AppError as exc:
                return {
                    **result,
                    "rejected": True,
                    "dispatched": False,
                    "boundary": "app client schema",
                    "error": exc.safe_message,
                }
            raise AssertionError("Invalid schema was accepted")
        now, message_id, question_id = datetime.now(UTC), uuid4().hex, uuid4().hex
        name = ToolName.CHECK_COUPON if body.action == "coupon" else ToolName.GET_ORDER_BENEFITS
        params = ToolParameters(
            order_id=body.order_id, coupon_id=body.coupon_id if body.action == "coupon" else None
        )
        entities = tuple(
            Entity(name=n, value=v, source=EntitySource(message_id=message_id, excerpt=v))
            for n, v in [
                (EntityName.ORDER_ID, body.order_id),
                *([(EntityName.COUPON_ID, body.coupon_id)] if params.coupon_id else []),
            ]
        )
        intent = IntentCode.COUPON_UNUSABLE if params.coupon_id else IntentCode.DISCOUNT_MISSING
        from deephelp_app.domain.models import VersionManifest

        question = Question(
            question_id=question_id,
            identity=identity,
            session_id="m22-protocol-" + message_id,
            status=QuestionStatus.ACTIVE,
            version=1,
            entities=entities,
            member_message_ids=(message_id,),
            active_intent=intent,
            versions=assembly.sop_registry.pin(VersionManifest(registry="complaints-v1"), intent),
            created_at=now,
            updated_at=now,
        )
        context = RequestEnvelope(
            identity=identity,
            channel="m22-protocol",
            session_id=question.session_id,
            message_id=message_id,
            raw_text="固定只读协议实验 " + body.order_id,
            occurred_at=now,
            received_at=now,
            request_id=request.state.request_id,
            trace_id=request.state.trace_id,
        )
        tool_request = ToolRequest(
            operation_id=uuid4().hex,
            run_id=uuid4().hex,
            question_id=question_id,
            identity=identity,
            tool_name=name,
            tool_version=TOOL_VERSION,
            parameters=params,
            parameters_hash=tool_parameters_hash(params),
            budget=budget.snapshot(),
        )
        if body.action == "server_invalid":
            call_id = "call-" + uuid4().hex
            envelope = ToolInvocationEnvelope(
                request=tool_request,
                request_id=context.request_id,
                trace_id=context.trace_id,
                call_id=call_id,
            )
            wire = await gateway._connected().call_tool(
                "get_order_benefits",
                {"order_id": 123},
                meta=gateway._metadata(
                    {
                        "action": "call_tool",
                        "nonce": call_id,
                        "envelope": envelope.model_dump(mode="json"),
                    }
                ),
            )
            error = (wire.structured_content or {}).get("error", {})
            if not wire.is_error or error.get("code") != "INVALID_ARGUMENT":
                raise AppError(
                    ErrorCode.MODEL_OUTPUT_INVALID,
                    "Server schema experiment did not reach the expected boundary",
                )
            return {
                **result,
                "rejected": True,
                "dispatched": True,
                "boundary": "MCP service input schema",
                "error": error,
                "call_id": call_id,
            }
        reply = await gateway.execute(tool_request, question, budget, context=context)
        return {
            **result,
            "input": params.model_dump(mode="json", exclude_none=True),
            "boundary": "client schema → identity/context → server schema → "
            "PostgreSQL → output schema",
            "dispatched": reply.call_id is not None,
            "result": reply.model_dump(mode="json"),
        }

    @router.get("/workflow")
    async def workflow(request: Request) -> dict[str, Any]:
        auth(request)
        ready()
        assert assembly.ledger is not None
        graph = None
        if rights_port:
            async with approval(request) as service:
                graph = service.graph(request.state.budget).get_graph().to_json()
        return {
            "sop": assembly.sop_registry.model_dump(mode="json"),
            "approval_graph": graph,
            "synthetic": True,
            "checkpoint": "官方 AsyncPostgresSaver；原批准权与效果账本独立",
            "code": [
                "application/sop.py:SOPExecutor.execute",
                "application/approval.py:ApprovalService.graph",
                "adapters/postgres_checkpoints.py:PostgresCheckpoints",
            ],
            "model_calls": 0,
        }

    @router.post("/demo")
    async def create_demo(request: Request) -> Any:
        if auth(request) != DEMO_IDENTITY:
            raise AppError(ErrorCode.FORBIDDEN, "Use the configured synthetic demo identity", 403)
        async with approval(request) as service:
            op = await prepare(service.repo, "m22-demo-" + uuid4().hex)
            await service.pause(op.plan.operation_id, request.state.budget)
            return op.model_dump(mode="json")

    @router.get("/demo/{operation_id}")
    async def demo_status(operation_id: str, request: Request, session_id: str, run_id: str) -> Any:
        ready()
        assert assembly.ledger is not None
        repo = ApprovalRepository(assembly.ledger.pool)
        op = await repo.scoped(auth(request), session_id, run_id, operation_id)
        if not session_id.startswith("m22-demo-") or op.plan.parameters.order_id != "DEMO-D01":
            raise AppError(ErrorCode.NOT_FOUND, "Learning demo operation not found", 404)
        async with official_checkpoints(
            getattr(repo.pool, "config", None) or application_config(assembly.root),
            owner=getattr(repo.pool, "owner", None),
        ) as saver:
            config = ApprovalService.config(op)
            checkpoints = []
            async for item in saver.alist(config, limit=10):
                checkpoints.append(
                    {
                        "id": item.checkpoint["id"],
                        "parent": item.parent_config,
                        "metadata": {
                            k: dict(item.metadata)[k]
                            for k in ("source", "step", "parents")
                            if k in item.metadata
                        },
                        "channels": list(item.checkpoint["channel_values"]),
                        "pending_writes": [
                            {"task": w[0], "channel": w[1]} for w in item.pending_writes or []
                        ],
                    }
                )
            graph_reader = ApprovalService(
                repo, cast(RightsClient, None), op.plan.snapshot_hash, saver=saver
            )
            state = await graph_reader.graph(request.state.budget).aget_state(config)
        async with repo.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT COUNT(*) FROM dh_m15_synthetic_effects WHERE operation_id=%s",
                (operation_id,),
            )
            effects = int((await cursor.fetchone())[0])
            await conn.rollback()
        return {
            "operation": op.model_dump(mode="json"),
            "checkpoints": checkpoints,
            "next": state.next if state else (),
            "interrupts": [i.value for t in state.tasks for i in t.interrupts] if state else [],
            "execution_enabled": rights_port is not None,
            "effects": effects,
            "history": await repo.history(operation_id),
            "model_calls": 0,
        }

    @router.post("/demo/{operation_id}/approval")
    async def decide(operation_id: str, body: ApprovalCommand, request: Request) -> Any:
        async with scoped(request, operation_id, body.session_id, body.run_id) as (service, _):
            return (await service.decide(auth(request), operation_id, body)).model_dump(mode="json")

    @router.post("/demo/{operation_id}/resume")
    async def resume(operation_id: str, body: ResumeCommand, request: Request) -> Any:
        async with scoped(request, operation_id, body.session_id, body.run_id) as (service, _):
            return (
                await service.resume(auth(request), operation_id, body, request.state.budget)
            ).model_dump(mode="json")

    return router

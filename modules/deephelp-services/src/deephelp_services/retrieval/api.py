"""Owned published-corpus retrieval through the official Streamable HTTP MCP SDK."""

import argparse
import asyncio
import hashlib
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
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

from deephelp_app.adapters.mcp.business.mcp_protocol import canonical
from deephelp_app.adapters.mcp.retrieval.retrieval_protocol import (
    HEALTH_URI,
    VERSION,
    arguments,
    registered_tools,
    validate_results,
)
from deephelp_app.adapters.mcp.shared.network_mcp import DelegationVerifier, ServiceCredentials
from deephelp_app.adapters.observability.telemetry import environment, server_call
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import ErrorCode, ErrorDetail
from deephelp_app.domain.models.retrieval_protocol import (
    RetrievalAction,
    RetrievalDelegation,
    RetrievalReply,
    RetrievalWire,
)
from deephelp_services.transport.http import (
    initialize_credentials,
    protect_and_mount,
    session_manager,
)

from .backend import RetrievalBackend


def make_server(backend: Any, credentials: ServiceCredentials) -> Server[dict[str, Any]]:
    verifier = DelegationVerifier(credentials, "retrieval")

    async def listing(
        ctx: ServerRequestContext[dict[str, Any]], params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        return ListToolsResult(tools=registered_tools(backend.binding))

    @server_call("mcp.retrieval.call")
    async def calling(
        ctx: ServerRequestContext[dict[str, Any]], params: CallToolRequestParams
    ) -> CallToolResult:
        try:
            query = arguments(params.name, params.arguments or {})
            payload = verifier.verify(ctx.meta)
            if set(payload) != {"action", "nonce", "delegation"} or payload["action"] != "retrieve":
                raise AppError(ErrorCode.FORBIDDEN, "Trusted retrieval delegation required")
            delegation = RetrievalDelegation.model_validate(payload["delegation"])
            identity = delegation.context.identity
            allowance = backend.binding.allowance(query.query, 60)
            if (
                identity.tenant_id,
                identity.user_id,
            ) not in backend.subjects or query.scope_id != backend.binding.scope.fingerprint:
                raise AppError(ErrorCode.FORBIDDEN, "Retrieval subject or scope not authorized")
            if (
                delegation.name != params.name
                or delegation.version != backend.binding.version
                or delegation.query_hash
                != hashlib.sha256(canonical(query.model_dump(mode="json"))).hexdigest()
                or delegation.profile_id != backend.binding.profile_id
            ):
                raise AppError(ErrorCode.VERSION_CONFLICT, "Retrieval request or profile differs")
            if backend.binding.rerank_policy and query.top_k != backend.binding.top_k:
                raise AppError(ErrorCode.VERSION_CONFLICT, "Final top_k differs from the release")
            cap = delegation.allowance
            if (
                cap.attempts > allowance.attempts
                or cap.tokens > allowance.tokens
                or cap.cost > allowance.cost
            ):
                raise AppError(
                    ErrorCode.BUDGET_EXHAUSTED, "Retrieval allowance exceeds owned bound"
                )
        except ValidationError, ValueError, TypeError:
            error = AppError(ErrorCode.INVALID_ARGUMENT, "Invalid retrieval delegation")
        except AppError as exc:
            error = exc
        else:
            actions = [RetrievalAction(action="mcp", elapsed_ms=0, attempts=1)]
            budget = ExecutionBudget.start(
                cap.seconds, cap.attempts, 0, token_limit=cap.tokens, cost_limit=cap.cost
            )
            budget.claim_attempt(retry=False)
            results, detail = {}, None
            try:
                async with asyncio.timeout_at(budget.deadline):
                    results = await backend.execute(params.name, query, budget, actions)
                validate_results(backend.binding, query, params.name, results)
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                detail = ErrorDetail(code=ErrorCode.TIMEOUT, message="Retrieval query timed out")
            except AppError as exc:
                detail = ErrorDetail(
                    code=exc.code, message="Retrieval execution rejected", retryable=exc.retryable
                )
            except Exception:
                detail = ErrorDetail(
                    code=ErrorCode.MODEL_OUTPUT_INVALID, message="Invalid retrieval result"
                )
            if detail:
                results = {}
            reply = RetrievalReply(
                version=backend.binding.version,
                call_id=payload["nonce"],
                delegation=delegation,
                query=query,
                service_pid=os.getpid(),
                results=results,
                used=budget.usage(),
                actions=tuple(actions),
                error=detail,
            )
            proof = credentials.protect(
                "retrieval-receipt",
                {
                    "action": "receipt",
                    "nonce": reply.call_id,
                    "reply_hash": hashlib.sha256(
                        canonical(reply.model_dump(mode="json"))
                    ).hexdigest(),
                },
            )
            wire = RetrievalWire(reply=reply, proof=dict(proof)).model_dump(mode="json")
            return CallToolResult(
                is_error=detail is not None,
                structured_content=wire,
                content=[TextContent(text=canonical(wire).decode())],
            )
        data = {
            "error": {
                "code": error.code.value,
                "message": error.safe_message,
                "retryable": error.retryable,
            }
        }
        return CallToolResult(
            is_error=True, structured_content=data, content=[TextContent(text=error.safe_message)]
        )

    async def reading(
        ctx: ServerRequestContext[dict[str, Any]], params: ReadResourceRequestParams
    ) -> ReadResourceResult:
        payload = verifier.verify(ctx.meta)
        if (
            set(payload) != {"action", "nonce", "uri"}
            or payload["action"] != "read_resource"
            or payload["uri"] != HEALTH_URI
            or str(params.uri) != HEALTH_URI
        ):
            raise AppError(ErrorCode.FORBIDDEN, "Trusted retrieval health delegation required")
        return ReadResourceResult(
            contents=[
                TextResourceContents(
                    uri=HEALTH_URI,
                    mime_type="application/json",
                    text=canonical({**backend.binding.health(), "pid": os.getpid()}).decode(),
                )
            ]
        )

    return Server(
        "deephelp-retrieval",
        version=VERSION,
        on_list_tools=listing,
        on_call_tool=calling,
        on_read_resource=reading,
    )


def create_retrieval_app(
    root: Path,
    credentials: ServiceCredentials,
    *,
    providers: Path,
    pointer: Path,
    corpus: Path,
    auth: Path,
    backend: Any = None,
) -> FastAPI:
    app = FastAPI(title="DeepHelp Retrieval", version=VERSION)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            stack.enter_context(environment("deephelp-retrieval-mcp"))
            owned = backend or await stack.enter_async_context(
                RetrievalBackend.open(root, providers, pointer, corpus, auth)
            )
            app.state.backend = owned
            manager = session_manager(make_server(owned, credentials))
            app.state.mcp_manager = manager
            async with manager.run():
                yield

    app.router.lifespan_context = lifespan
    protect_and_mount(app, credentials)
    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="Independent published-corpus Retrieval MCP")
    parser.add_argument("command", choices=["init", "serve"])
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--providers", type=Path, default=Path("modules/deephelp-app/providers.example.json")
    )
    parser.add_argument("--pointer", type=Path, default=Path(".local/m09/active.json"))
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--auth", type=Path)
    parser.add_argument("--port", type=int, default=8012)
    args = parser.parse_args()
    if args.command == "init":
        initialize_credentials(args.credentials)
        print("Retrieval credentials initialized; values remain only in the credentials file")
        return 0
    if args.corpus is None or args.auth is None:
        raise ConfigurationError("Retrieval serve requires explicit --corpus and --auth")
    app = create_retrieval_app(
        args.root,
        ServiceCredentials.load(args.credentials),
        providers=args.providers,
        pointer=args.pointer,
        corpus=args.corpus,
        auth=args.auth,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AppError, ConfigurationError:
        print("Retrieval service configuration/dependency failure")
        raise SystemExit(1) from None

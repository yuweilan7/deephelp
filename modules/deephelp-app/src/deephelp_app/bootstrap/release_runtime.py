"""Select a complete MySQL release once per request; retain independent runtime instances."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, cast

from mcp import StdioServerParameters

from deephelp_app.adapters.network_mcp import HTTPMCPConfig
from deephelp_app.adapters.release_assets import verify_release
from deephelp_app.adapters.release_store import ReleaseMismatch, ReleaseRepository, ReleaseStore
from deephelp_app.application.artifact_paths import resolve_artifact
from deephelp_app.application.conversation import Conversation
from deephelp_app.bootstrap.mvp_runtime import LiveAssembly
from deephelp_app.domain.errors import ConfigurationError


class ReleasedApprovals:
    def __init__(self, router: Any, repo: ReleaseRepository) -> None:
        self.router, self.repo = router, repo

    async def service(self, operation: str) -> Any:
        op = await self.repo.get(operation)
        identity = op.pending_response.versions.release_manifest
        if identity is None:
            raise ConfigurationError("Operation has no complete release binding")
        conversation = await self.router.entry(identity)
        if conversation.approvals is None:
            raise ConfigurationError("Synthetic rights endpoint is not configured")
        return conversation.approvals

    async def decide(self, identity: Any, operation: str, command: Any) -> Any:
        await self.repo.scoped(identity, command.session_id, command.run_id, operation)
        return await (await self.service(operation)).decide(identity, operation, command)

    async def resume(self, identity: Any, operation: str, command: Any, budget: Any) -> Any:
        await self.repo.scoped(identity, command.session_id, command.run_id, operation)
        return await (await self.service(operation)).resume(identity, operation, command, budget)


class ReleaseRuntime:
    def __init__(
        self,
        channel: str,
        *,
        rights_port: int | None = None,
        rights_key: Path | None = None,
        after_accept: Any = None,
        tool_server: StdioServerParameters | None = None,
        corpus_path: Path | None = None,
        business_mcp: HTTPMCPConfig | None = None,
        retrieval_mcp: HTTPMCPConfig | None = None,
    ) -> None:
        self.channel, self.rights_port, self.rights_key = channel, rights_port, rights_key
        self.after_accept = after_accept
        self.tool_server, self.corpus_path = tool_server, corpus_path
        self.business_mcp, self.retrieval_mcp = business_mcp, retrieval_mcp
        self.assemblies: dict[str, LiveAssembly] = {}
        self.initial_assembly: LiveAssembly | None = None

    def __getattr__(self, name: str) -> Any:
        # Learning labs refer to the assembly selected at startup. Conversation routing
        # continues to bind each run to the authoritative release as before.
        initial = self.__dict__.get("initial_assembly")
        if initial is None or name.startswith("_"):
            raise AttributeError(name)
        return getattr(initial, name)

    @asynccontextmanager
    async def open(self, client: Any, trace: Any) -> Any:
        async with AsyncExitStack() as stack:
            store = await ReleaseStore.open(Path.cwd())
            stack.push_async_callback(store.aclose)
            cache: dict[str, Conversation] = {}
            lock = asyncio.Lock()
            runtime = self

            class Router:
                initial: Conversation
                approvals: Any = None

                def __getattr__(self, name: str) -> Any:
                    return getattr(self.initial, name)

                async def entry(self, identity: str) -> Conversation:
                    async with lock:
                        if identity not in cache:
                            manifest = await store.manifest(identity)
                            data = verify_release(manifest)
                            assembly = LiveAssembly(
                                Path.cwd(),
                                resolve_artifact(manifest.providers_path),
                                resolve_artifact(data["dense_pointer"]),
                                fasttext_pointer=resolve_artifact(data["fasttext_pointer"]),
                                reviewed_rules=resolve_artifact(data["rules"]),
                                event_collection="dh_m10_events_m18_release_" + identity[:20],
                                tool_server=runtime.tool_server,
                                business_mcp=runtime.business_mcp,
                                retrieval_mcp=runtime.retrieval_mcp,
                                corpus_path=runtime.corpus_path,
                                rights_port=runtime.rights_port,
                                rights_key=runtime.rights_key,
                                release_manifest=manifest,
                                release_channel=runtime.channel,
                            )
                            # MCP uses task-local cancellation scopes. A resource owner must
                            # enter and exit the entire assembly in the same background task.
                            ready: asyncio.Future[Conversation] = (
                                asyncio.get_running_loop().create_future()
                            )
                            finished = asyncio.Event()

                            async def own_resources() -> None:
                                try:
                                    async with assembly.open(client, trace) as service:
                                        ready.set_result(service)
                                        await finished.wait()
                                except BaseException as exc:
                                    if not ready.done():
                                        ready.set_exception(exc)
                                    else:
                                        raise

                            owner = asyncio.create_task(own_resources())

                            async def close_resources() -> None:
                                finished.set()
                                await owner

                            stack.push_async_callback(close_resources)
                            conversation = await asyncio.shield(ready)
                            assert isinstance(conversation.ledger, ReleaseRepository)
                            conversation.ledger.after_accept = runtime.after_accept
                            cache[identity] = conversation
                            runtime.assemblies[identity] = assembly
                        return cache[identity]

                async def run(self, request: Any, budget: Any) -> Any:
                    ledger = cast(ReleaseRepository, self.initial.ledger)
                    prior = await ledger.lookup(request)
                    if prior and prior.response:
                        # Receipt replay consumes no versioned model/tool assets.
                        return await self.initial.run(request, budget)
                    if prior:
                        identity = prior.question.versions.release_manifest
                    elif request.question_hint:
                        q = await self.initial.ledger.question(
                            request.identity, request.session_id, request.question_hint
                        )
                        identity = q.versions.release_manifest if q else None
                        if identity is None:
                            raise ConfigurationError(
                                "Continuation has no authorized complete release"
                            )
                    else:
                        identity = (await store.active(runtime.channel)).active
                    if identity is None:
                        raise ConfigurationError("No complete release is active")
                    if not prior and not request.question_hint:
                        selected = await store.manifest(identity)
                        await store.require_current(selected.review_snapshot)
                    conversation = await self.entry(identity)
                    try:
                        return await conversation.run(request, budget)
                    except ReleaseMismatch as mismatch:
                        # Automatic continuation belongs to the original complete version.
                        return await (await self.entry(mismatch.expected)).run(request, budget)

            router = Router()
            active = await store.active(self.channel)
            if active.active is None:
                raise ConfigurationError("Publish a complete version before serving")
            router.initial = await router.entry(active.active)
            runtime.initial_assembly = runtime.assemblies[active.active]
            assert isinstance(router.initial.ledger, ReleaseRepository)
            if self.rights_port:
                router.approvals = ReleasedApprovals(router, router.initial.ledger)
            yield cast(Conversation, router)

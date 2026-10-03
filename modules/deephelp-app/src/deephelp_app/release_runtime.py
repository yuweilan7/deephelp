"""Select a complete MySQL release once per request; retain independent runtime instances."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, cast

from deephelp_app.conversation import Conversation
from deephelp_app.errors import ConfigurationError
from deephelp_app.mvp_runtime import LiveAssembly
from deephelp_app.release_assets import verify_release
from deephelp_app.release_store import ReleaseMismatch, ReleaseRepository, ReleaseStore


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
    ) -> None:
        self.channel, self.rights_port, self.rights_key = channel, rights_port, rights_key
        self.after_accept = after_accept
        self.assemblies: dict[str, LiveAssembly] = {}

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
                                Path(manifest.providers_path),
                                Path(data["dense_pointer"]),
                                fasttext_pointer=Path(data["fasttext_pointer"]),
                                reviewed_rules=Path(data["rules"]),
                                event_collection="dh_m10_events_m18_release_" + identity[:20],
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
            assert isinstance(router.initial.ledger, ReleaseRepository)
            if self.rights_port:
                router.approvals = ReleasedApprovals(router, router.initial.ledger)
            yield cast(Conversation, router)

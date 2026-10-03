"""Explicit, finite M10 projection worker and authority-checked related-case read."""

import argparse
import asyncio
import json
from contextlib import AsyncExitStack
from pathlib import Path

import httpx

from deephelp_app.cases import MySQLCaseRepository
from deephelp_app.domain.models import VerifiedIdentity
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.execution import AsyncCalls
from deephelp_app.local_paths import local_path
from deephelp_app.memory import MemoryService, ProjectionWorker, RedisMemory
from deephelp_app.milvus_dense import create_client
from deephelp_app.milvus_memory import MilvusEventIndex
from deephelp_app.mvp_runtime import BudgetSession
from deephelp_app.providers import ProviderConfig, create_gateway


async def run(args: argparse.Namespace) -> None:
    gate = BudgetSession(local_path(args.budget_state))
    gate.open()
    try:
        async with asyncio.timeout(300), AsyncExitStack() as stack:
            repository = await MySQLCaseRepository.open(Path.cwd())
            stack.push_async_callback(repository.aclose)
            cache = RedisMemory.open(Path.cwd())
            stack.push_async_callback(cache.aclose)
            client = await stack.enter_async_context(httpx.AsyncClient(trust_env=False, timeout=30))
            config = ProviderConfig.load(Path(args.providers))
            gateway = create_gateway(client, config, AsyncCalls(2, 30))
            milvus = create_client(Path.cwd())
            stack.push_async_callback(milvus.close)
            index = MilvusEventIndex(milvus, gateway, config.signature(), args.collection)
            await index.initialize()
            memory = MemoryService(repository, cache, index)
            budget = gate.request()
            if args.command == "project":
                worker = ProjectionWorker(repository, MemoryService(repository, cache), index)
                if args.limit <= 0:
                    raise ConfigurationError("Positive worker limit required")
                count = 0
                for _ in range(args.limit):
                    result = await worker.once(gate.request())
                    if result == "idle":
                        break
                    if result == "projected":
                        count += 1
                print(
                    json.dumps(
                        {
                            "projected": count,
                            "limit": args.limit,
                            "cumulative_budget": gate.state,
                        }
                    )
                )
            elif args.command == "rebuild":
                identity = VerifiedIdentity(tenant_id=args.tenant, user_id=args.user)
                questions = await repository.latest_questions(
                    identity, args.session, limit=args.limit + 1
                )
                if len(questions) > args.limit:
                    raise ConfigurationError(
                        "Rebuild range exceeds --limit; increase it within the cumulative budget"
                    )
                for question in questions:
                    await index.put(question, gate.request())
                await memory.load(identity, args.session, gate.request())
                if memory.last_cache_error:
                    raise ConfigurationError("Rebuild Redis projection failed")
                print(json.dumps({"rebuilt": len(questions), "session": args.session}))
            else:
                identity = VerifiedIdentity(tenant_id=args.tenant, user_id=args.user)
                window = await memory.load(identity, args.session, budget, query=args.query)
                print(window.model_dump_json(indent=2))
    finally:
        gate.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="DeepHelp M10 memory")
    parser.add_argument("command", choices=["project", "show", "rebuild"])
    parser.add_argument("--budget-state", required=True)
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--collection")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--tenant", default="synthetic-tenant")
    parser.add_argument("--user", default="synthetic-user-a")
    parser.add_argument("--session", default="demo")
    parser.add_argument("--query")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
        return 0
    except (AppError, ConfigurationError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

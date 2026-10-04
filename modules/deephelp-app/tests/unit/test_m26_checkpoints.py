"""Official saver lifecycle and pool configuration boundaries; no libpq connection."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deephelp_app.adapters import postgres_checkpoints as checkpoints
from deephelp_app.adapters.postgres import PostgresConfig


@pytest.mark.parametrize("memory", [False, True])
def test_postgres_approval_requires_official_saver(memory):
    from langgraph.checkpoint.memory import InMemorySaver

    from deephelp_app.application.approval import ApprovalService
    from deephelp_app.domain.errors import AppError

    repo = SimpleNamespace(pool=SimpleNamespace(backend="postgresql"))
    with pytest.raises(AppError, match="explicitly opened official saver"):
        ApprovalService(repo, None, "original-snapshot", saver=InMemorySaver() if memory else None)


@pytest.mark.parametrize("schema", ["public; DROP SCHEMA public", "Upper", "../other", "x" * 64])
async def test_checkpoint_schema_is_validated_before_constructing_pool(schema):
    with pytest.raises(ValueError, match="schema"):
        async with checkpoints.official_checkpoints(
            PostgresConfig("localhost", 15432, "isolated", "user", "secret"), schema=schema
        ):
            pytest.fail("invalid schema accepted")


@pytest.mark.parametrize("error", [TimeoutError(), __import__("asyncio").CancelledError()])
async def test_open_failure_or_cancellation_closes_official_pool(monkeypatch, error):
    options = {}
    pool = SimpleNamespace(open=AsyncMock(side_effect=error), close=AsyncMock())

    class Factory:
        @classmethod
        def __class_getitem__(cls, _):
            return cls

        def __new__(cls, **kwargs):
            options.update(kwargs)
            return pool

    monkeypatch.setattr(checkpoints, "AsyncConnectionPool", Factory)
    with pytest.raises(type(error)):
        async with checkpoints.official_checkpoints(
            PostgresConfig("localhost", 15432, "isolated", "user", "secret")
        ):
            pytest.fail("failed open returned a saver")
    pool.close.assert_awaited_once_with(timeout=5)
    assert options["kwargs"]["autocommit"] is True
    assert options["kwargs"]["prepare_threshold"] == 0
    assert options["kwargs"]["row_factory"] is checkpoints.dict_row
    assert options["kwargs"]["options"].endswith("search_path=deephelp_checkpoints")
    assert options["open"] is False and options["max_size"] == 4


async def test_missing_saver_migrations_close_ledger_before_opening_projections(
    tmp_path, monkeypatch
):
    from contextlib import asynccontextmanager

    from deephelp_app.adapters.approval_store import ApprovalRepository
    from deephelp_app.bootstrap import mvp_runtime

    ledger = object.__new__(ApprovalRepository)
    ledger.aclose = AsyncMock()
    monkeypatch.setattr(ApprovalRepository, "open", AsyncMock(return_value=ledger))

    @asynccontextmanager
    async def missing(*args, **kwargs):
        raise ValueError("Run explicit checkpoint migrations")
        yield

    monkeypatch.setattr(mvp_runtime.ApprovalService, "open", missing)
    monkeypatch.setattr(
        mvp_runtime.RedisMemory, "open", lambda _: pytest.fail("Opened projections")
    )
    key = tmp_path / "rights.key"
    key.write_bytes(b"k" * 32)
    assembly = SimpleNamespace(
        tool_server=object(),
        business_mcp=None,
        release_manifest=None,
        rights_port=12345,
        rights_key=key,
        root=tmp_path,
        sop_registry=SimpleNamespace(snapshot_hash="original-snapshot"),
    )
    with pytest.raises(ValueError, match="explicit checkpoint migrations"):
        async with mvp_runtime.LiveAssembly.open(assembly, object(), object()):
            pytest.fail("Missing migrations started the conversation")
    ledger.aclose.assert_awaited_once()

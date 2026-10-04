"""Official saver lifecycle and pool configuration boundaries; no libpq connection."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deephelp_app.adapters import postgres_checkpoints as checkpoints
from deephelp_app.adapters.postgres import PostgresConfig


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

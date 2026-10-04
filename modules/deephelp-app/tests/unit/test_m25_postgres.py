"""Offline safeguards for explicit PG configuration, lifecycle and Windows runner."""

import asyncio
import json
import threading
from unittest.mock import AsyncMock

import psycopg
import pytest

from deephelp_app.adapters import postgres
from deephelp_app.adapters.postgres import PostgresConfig, postgres_pool, run_postgres


async def test_owned_selector_cancellation_settles_before_close():
    owner = postgres.PoolLoop()
    started, cleaned = threading.Event(), threading.Event()

    async def operation():
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            await asyncio.sleep(0.02)
            cleaned.set()

    task = asyncio.create_task(owner.call(operation()))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set()
    finally:
        await owner.close()
    assert not owner.thread.is_alive() and owner.loop.is_closed()


def test_application_has_no_implicit_backend_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPHELP_POSTGRES_CONFIG", raising=False)
    with pytest.raises(ValueError, match="explicitly"):
        postgres.application_config(tmp_path)


async def test_owned_loop_cancel_before_operation_starts():
    owner = postgres.PoolLoop()
    unblock, entered = threading.Event(), threading.Event()
    owner.loop.call_soon_threadsafe(lambda: unblock.wait(1))

    async def operation():
        entered.set()

    task = asyncio.create_task(owner.call(operation()))
    try:
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        unblock.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not entered.is_set()
    finally:
        unblock.set()
        await owner.close()
    assert owner.loop.is_closed()


def config() -> PostgresConfig:
    return PostgresConfig("localhost", 15432, "deephelp_m25_test", "app", "secret-value")


@pytest.mark.parametrize(
    "field,value",
    [
        ("port", True),
        ("port", 0),
        ("port", 65536),
        ("port", "5432"),
        ("host", ""),
        ("dbname", "db\x00other"),
        ("password", None),
        ("user", 1),
    ],
)
def test_invalid_connection_fields(field, value):
    values = config().connection_kwargs()
    values = {key: values[key] for key in ("host", "port", "dbname", "user", "password")}
    values[field] = value
    with pytest.raises(ValueError, match="Invalid PostgreSQL"):
        PostgresConfig(**values)


def test_config_is_explicit_and_secret_repr_hidden(tmp_path):
    path = tmp_path / "postgres.json"
    values = {
        "host": "localhost",
        "port": 15432,
        "dbname": "deephelp_m25_test",
        "user": "app",
        "password": "secret-value",
    }
    path.write_text(json.dumps(values), encoding="utf-8")
    assert PostgresConfig.read(path) == config()
    assert "secret-value" not in repr(config())
    values["postgresql_fallback"] = True
    path.write_text(json.dumps(values), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly"):
        PostgresConfig.read(path)


async def test_failed_pool_open_still_closes(monkeypatch):
    pool = AsyncMock()
    pool.open.side_effect = TimeoutError
    factory = type(
        "Factory",
        (),
        {
            "__class_getitem__": classmethod(lambda cls, _: cls),
            "__new__": lambda cls, **kwargs: pool,
        },
    )
    monkeypatch.setattr(postgres, "AsyncConnectionPool", factory)
    with pytest.raises(TimeoutError):
        async with postgres_pool(config()):
            pytest.fail("Failed open must never yield")
    pool.close.assert_awaited_once_with(timeout=5)


async def test_cancelled_user_transaction_closes_pool(monkeypatch):
    pool = AsyncMock()
    factory = type(
        "Factory",
        (),
        {
            "__class_getitem__": classmethod(lambda cls, _: cls),
            "__new__": lambda cls, **kwargs: pool,
        },
    )
    monkeypatch.setattr(postgres, "AsyncConnectionPool", factory)
    with pytest.raises(asyncio.CancelledError):
        async with postgres_pool(config()):
            raise asyncio.CancelledError
    pool.close.assert_awaited_once_with(timeout=5)


@pytest.mark.parametrize("max_size,wait_seconds", [(0, 5), (True, 5), (4, 0), (4, float("nan"))])
async def test_invalid_limits_never_connect(max_size, wait_seconds):
    with pytest.raises(ValueError):
        async with postgres_pool(config(), max_size=max_size, wait_seconds=wait_seconds):
            pytest.fail("Invalid limits must never connect")


def test_windows_runner_uses_selector_without_global_policy(monkeypatch):
    # Exercise the decision on Linux CI too; a native Windows run qualifies the actual loop.
    monkeypatch.setattr(postgres.sys, "platform", "win32")

    async def operation():
        return asyncio.get_running_loop()

    loop = run_postgres(operation())
    assert isinstance(loop, asyncio.SelectorEventLoop) and loop.is_closed()


@pytest.mark.parametrize("host", ["postgres.invalid", "127.0.0.1"])
async def test_offline_guard_blocks_libpq_before_network(host):
    # C libpq bypasses Python socket guards; loopback can be a live SSH tunnel too.
    with pytest.raises(RuntimeError, match="Offline pytest blocks PostgreSQL"):
        await psycopg.AsyncConnection.connect(host=host, dbname="unused")
    with pytest.raises(RuntimeError, match="Offline pytest blocks PostgreSQL"):
        psycopg.Connection.connect(host=host, dbname="unused")
    with pytest.raises(RuntimeError, match="Offline pytest blocks PostgreSQL"):
        psycopg.connect(host=host, dbname="unused")

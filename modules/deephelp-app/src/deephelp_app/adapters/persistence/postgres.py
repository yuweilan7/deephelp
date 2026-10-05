"""Explicit PostgreSQL configuration, short transactions and bounded lifecycle."""

import asyncio
import json
import math
import os
import sys
import threading
from collections.abc import AsyncIterator, Coroutine
from concurrent.futures import Future
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import set_json_loads
from psycopg_pool import AsyncConnectionPool


@dataclass(frozen=True)
class PostgresConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            any(
                not isinstance(v, str) or not v or "\x00" in v
                for v in (self.host, self.dbname, self.user, self.password)
            )
            or type(self.port) is not int
            or not 1 <= self.port <= 65535
        ):
            raise ValueError("Invalid PostgreSQL connection configuration")

    @classmethod
    def read(cls, path: Path) -> PostgresConfig:
        body = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(body, dict) or set(body) != {
            "host",
            "port",
            "dbname",
            "user",
            "password",
        }:
            raise ValueError("PostgreSQL config requires exactly host/port/dbname/user/password")
        return cls(**body)

    def connection_kwargs(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.user,
            "password": self.password,
            "connect_timeout": 5,
            "autocommit": False,
            "options": "-c timezone=UTC -c statement_timeout=10000 "
            "-c lock_timeout=3000 -c idle_in_transaction_session_timeout=15000",
        }


@asynccontextmanager
async def postgres_pool(
    config: PostgresConfig, *, max_size: int = 4, wait_seconds: float = 5
) -> AsyncIterator[AsyncConnectionPool[AsyncConnection[Any]]]:
    if (
        type(max_size) is not int
        or max_size < 1
        or not math.isfinite(wait_seconds)
        or wait_seconds <= 0
    ):
        raise ValueError("Invalid PostgreSQL pool limits")
    pool = AsyncConnectionPool[AsyncConnection[Any]](
        kwargs=config.connection_kwargs(),
        min_size=1,
        max_size=max_size,
        open=False,
        timeout=wait_seconds,
        reconnect_timeout=5,
        max_lifetime=300,
        name="deephelp-postgres",
    )
    try:
        await pool.open(wait=True, timeout=wait_seconds)
        yield pool
    finally:
        await pool.close(timeout=wait_seconds)


def run_postgres[T](operation: Coroutine[Any, Any, T]) -> T:
    # Runner avoids deprecated global event-loop policy changes on Python 3.14.
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=factory) as runner:
        return runner.run(operation)


def application_config(root: Path) -> PostgresConfig:
    path = os.environ.get("DEEPHELP_POSTGRES_CONFIG")
    if not path:
        raise ValueError("DEEPHELP_POSTGRES_CONFIG must explicitly select the application database")
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    return PostgresConfig.read(candidate)


class PoolLoop:
    """Own libpq's selector on Windows while the caller keeps subprocess support."""

    def __init__(self) -> None:
        self.loop = asyncio.SelectorEventLoop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name="deephelp-postgres", daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            raise TimeoutError("PostgreSQL event loop did not start")

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.ready.set()
        try:
            self.loop.run_forever()
        finally:
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.close()

    async def call[T](self, operation: Coroutine[Any, Any, T]) -> T:
        done = threading.Event()
        future: Future[T] = Future()
        tasks: list[asyncio.Task[T]] = []

        def finish(task: asyncio.Task[T]) -> None:
            try:
                if task.cancelled():
                    future.set_exception(asyncio.CancelledError())
                elif (exc := task.exception()) is not None:
                    future.set_exception(exc)
                else:
                    future.set_result(task.result())
            finally:
                done.set()

        def start() -> None:
            if not future.set_running_or_notify_cancel():
                operation.close()
                done.set()
                return
            task = self.loop.create_task(operation)
            tasks.append(task)
            task.add_done_callback(finish)

        self.loop.call_soon_threadsafe(start)
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            future.cancel()
            self.loop.call_soon_threadsafe(lambda: [task.cancel() for task in tasks])
            # Wait for libpq cancellation/rollback before reusing this connection.
            if not await asyncio.to_thread(done.wait, 5):
                raise TimeoutError("PostgreSQL cancellation did not settle") from None
            raise

    async def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        await asyncio.to_thread(self.thread.join, 5)
        if self.thread.is_alive():
            raise TimeoutError("PostgreSQL event loop did not close")


async def on_pool_loop[T](owner: PoolLoop | None, operation: Coroutine[Any, Any, T]) -> T:
    return await owner.call(operation) if owner else await operation


class PostgresCursor:
    def __init__(self, cursor: Any, owner: PoolLoop | None) -> None:
        self.cursor, self.owner = cursor, owner

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self.cursor, name)
        if callable(attribute):

            async def call(*args: Any, **kwargs: Any) -> Any:
                return await on_pool_loop(self.owner, attribute(*args, **kwargs))

            return call
        return attribute

    async def __aenter__(self) -> PostgresCursor:
        await on_pool_loop(self.owner, self.cursor.__aenter__())
        return self

    async def __aexit__(self, *args: Any) -> Any:
        return await on_pool_loop(self.owner, self.cursor.__aexit__(*args))


class PostgresConnection:
    """Existing short-transaction port, backed exclusively by psycopg."""

    def __init__(self, connection: AsyncConnection[Any], owner: PoolLoop | None = None) -> None:
        self.connection, self.owner = connection, owner

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self.connection, name)
        if callable(attribute):

            async def call(*args: Any, **kwargs: Any) -> Any:
                return await on_pool_loop(self.owner, attribute(*args, **kwargs))

            return call
        return attribute

    def cursor(self, *args: Any, **kwargs: Any) -> PostgresCursor:
        return PostgresCursor(self.connection.cursor(*args, **kwargs), self.owner)

    async def begin(self) -> None:
        await on_pool_loop(self.owner, self.connection.execute("BEGIN"))


class PostgresPool:
    backend = "postgresql"

    def __init__(
        self,
        pool: AsyncConnectionPool[AsyncConnection[Any]],
        owner: PoolLoop | None = None,
        config: PostgresConfig | None = None,
    ) -> None:
        self.inner, self.owner, self.config = pool, owner, config

    @classmethod
    async def open(cls, config: PostgresConfig) -> PostgresPool:
        async def configure(connection: AsyncConnection[Any]) -> None:
            # JSON text is a persistent contract of the existing DTO/hash readers.
            set_json_loads(
                lambda data: data if isinstance(data, str) else data.decode("utf-8"), connection
            )

        owner = (
            PoolLoop()
            if sys.platform == "win32"
            and not isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)
            else None
        )

        async def open_pool() -> AsyncConnectionPool[AsyncConnection[Any]]:
            pool = AsyncConnectionPool[AsyncConnection[Any]](
                kwargs=config.connection_kwargs(),
                min_size=1,
                max_size=4,
                open=False,
                timeout=5,
                reconnect_timeout=5,
                configure=configure,
            )
            try:
                await pool.open(wait=True, timeout=5)
            except BaseException:
                await pool.close(timeout=5)
                raise
            return pool

        try:
            return cls(await on_pool_loop(owner, open_pool()), owner, config)
        except BaseException:
            if owner:
                await owner.close()
            raise

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[PostgresConnection]:
        connection = await on_pool_loop(self.owner, self.inner.getconn())
        try:
            yield PostgresConnection(connection, self.owner)
        finally:
            try:
                if not connection.closed:
                    await on_pool_loop(self.owner, connection.rollback())
            finally:
                await on_pool_loop(self.owner, self.inner.putconn(connection))

    async def aclose(self) -> None:
        try:
            await on_pool_loop(self.owner, self.inner.close(timeout=5))
        finally:
            if self.owner:
                await self.owner.close()

"""Explicit PostgreSQL configuration and bounded async pool lifecycle.

M25 protocol qualification only: current MySQL assembly does not import this module.
"""

import asyncio
import json
import math
import sys
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from psycopg import AsyncConnection
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

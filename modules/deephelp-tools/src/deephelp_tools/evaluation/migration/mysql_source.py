"""旧MySQL唯一显式只读源，供受校验快照导出使用。

连接配置从通用装配读取，不因导入模块连接数据库；旧源保留，不提供业务写入口。
"""

import asyncio
from pathlib import Path
from typing import Any

import aiomysql  # type: ignore[import-untyped]

from deephelp_app.bootstrap.connections import local_connection


class MySQLSource:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    @classmethod
    async def open(cls, root: Path) -> MySQLSource:
        c = local_connection(root)
        pool = await aiomysql.create_pool(
            host=c["MYSQL_HOST"],
            port=int(c["MYSQL_PORT"]),
            user=c["MYSQL_USER"],
            password=c["MYSQL_PASSWORD"],
            db=c["MYSQL_DATABASE"],
            charset="utf8mb4",
            autocommit=False,
            minsize=1,
            maxsize=1,
            connect_timeout=5,
            init_command="SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        )
        return cls(pool)

    async def aclose(self) -> None:
        self.pool.close()
        try:
            async with asyncio.timeout(5):
                await self.pool.wait_closed()
        except TimeoutError:
            self.pool.terminate()
            raise

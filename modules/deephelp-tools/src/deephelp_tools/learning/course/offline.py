"""课程进程拒绝网络；受控stdio demo只读包内合成数据。"""

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import psycopg


def deny_network(*args: object, **kwargs: object) -> None:
    raise RuntimeError("课程默认离线，禁止网络；真实验收请使用明确的probe入口")


async def deny_postgres(*args: object, **kwargs: object) -> None:
    deny_network()


@contextmanager
def offline_only() -> Iterator[None]:
    # libpq在C层连接，不经过Python socket；同时封住它的同步/异步入口。
    # patch的拥有者是CLI进程，退出上下文恢复原函数，不修改全局环境/配置。
    with (
        patch.object(socket, "getaddrinfo", deny_network),
        patch.object(socket.socket, "connect", deny_network),
        patch.object(socket.socket, "connect_ex", deny_network),
        patch.object(psycopg, "connect", deny_network),
        patch.object(psycopg.Connection, "connect", deny_network),
        patch.object(psycopg.AsyncConnection, "connect", deny_postgres),
    ):
        yield

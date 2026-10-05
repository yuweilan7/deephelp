"""旧独立基础设施命令的显式连接配置，不是业务装配入口。

settings沿项目连接文件/受保护secret读取，admin只在明确调用时选择原管理账号。
Redis/Milvus连接函数按需导入SDK；源码审阅不执行本模块的连接动作。
"""

import os
import pathlib

BASE = pathlib.Path(__file__).resolve().parents[1]


def read_env(p):
    return dict(
        line.split("=", 1)
        for line in p.read_text(encoding="utf-8-sig").splitlines()
        if line and not line.startswith("#") and "=" in line
    )


def settings(admin=False):
    p = BASE / "config/connections.env"
    if not p.exists():
        p = BASE / "config/connections.env.example"
    d = read_env(p)
    secret = pathlib.Path(
        os.getenv("DEEPHELP_SECRET_FILE", str(pathlib.Path.home() / ".deephelp/.env.secret"))
    )
    d.update(read_env(secret))
    return d


def redis_connect(admin=False):
    import redis

    d = settings()
    return redis.Redis(
        host=d["REDIS_HOST"],
        port=int(d["REDIS_PORT"]),
        username="deephelp_admin" if admin else d["REDIS_USER"],
        password=d["REDIS_ADMIN_PASSWORD" if admin else "REDIS_PASSWORD"],
        decode_responses=True,
        socket_timeout=10,
    )


def milvus_connect(admin=False, db=None):
    from pymilvus import MilvusClient

    d = settings()
    return MilvusClient(
        uri=d["MILVUS_URI"],
        user="root" if admin else d["MILVUS_USER"],
        password=d["MILVUS_ROOT_PASSWORD" if admin else "MILVUS_PASSWORD"],
        db_name=db or ("default" if admin else d["MILVUS_DATABASE"]),
        timeout=30,
    )

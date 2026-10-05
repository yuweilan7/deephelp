"""旧显式认证反例脚本，执行时连接Redis/Milvus并读取原权限。

拒绝匿名与允许已鉴权应用是两个断言；捕获异常不证明所有安全策略通过。
凭据来自受保护settings，报告只写布尔结果，不用于当前业务启动。
"""

import json
import logging

import redis
from pymilvus import MilvusClient
from settings import BASE, milvus_connect, settings

logging.disable(logging.CRITICAL)
d = settings()
r = {}


def rejected(fn):
    try:
        conn = fn()
        if hasattr(conn, "close"):
            conn.close()
        return False
    except Exception:
        return True


r["redis_unauthenticated_rejected"] = rejected(
    lambda: redis.Redis(host="127.0.0.1", port=int(d["REDIS_PORT"]), socket_timeout=5).ping()
)
r["milvus_unauthenticated_rejected"] = rejected(
    lambda: MilvusClient(uri=d["MILVUS_URI"], timeout=5).list_databases()
)
c = milvus_connect()
r["milvus_app_admin_access"] = "deephelp_app" in c.list_users()
c.close()
assert all(r.values()), r
(BASE / "reports/auth-negative-tests.json").write_text(json.dumps(r, indent=2))
print("AUTH_NEGATIVE_TESTS PASS")

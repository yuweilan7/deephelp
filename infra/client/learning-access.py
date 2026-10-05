"""Apply or verify Redis and Milvus application access in the private learning environment."""

# 旧基础设施显式apply/verify入口，复用原应用账号与认证来源。
#
# apply修改原权限后verify实际协议，读取不等于应用业务验收；SSH命令由当前入口拥有。
# 源码注释不触发权限变更，本轮没有执行apply。

import argparse
import importlib.util
import json
import logging
import subprocess
from pathlib import Path
from uuid import uuid4

import settings as local_settings
from pymilvus import MilvusClient


def ssh_python(c, root, source, *arguments):
    command = "sudo python3 -" + "".join(" " + a for a in arguments)
    result = subprocess.run(
        [
            "ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UserKnownHostsFile=" + str(root / "infra/client/known_hosts"),
            "-o",
            "ConnectTimeout=12",
            "-i",
            c["DEEPHELP_SSH_KEY_WINDOWS"],
            "-p",
            c["DEEPHELP_SSH_PORT"],
            c["DEEPHELP_SSH_USER"] + "@" + c["DEEPHELP_SSH_HOST"],
            command,
        ],
        input=source,
        text=True,
        capture_output=True,
        timeout=45,
    )
    if result.returncode:
        diagnostics = root / ".local/middleware-learning-access"
        diagnostics.mkdir(parents=True, exist_ok=True)
        (diagnostics / "remote-error.txt").write_text(result.stderr, encoding="utf-8")
        raise RuntimeError("Remote learning policy update failed; private diagnostics required")
    return json.loads(result.stdout)


def milvus_client(c, admin=False, database=None):
    return MilvusClient(
        uri=c["MILVUS_URI"],
        user="root" if admin else c["MILVUS_USER"],
        password=c["MILVUS_ROOT_PASSWORD"] if admin else c["MILVUS_PASSWORD"],
        db_name=database or c["MILVUS_DATABASE"],
        timeout=15,
    )


def apply_policy(root):
    c = local_settings.settings()
    source_root = Path(__file__).resolve().parents[2]
    redis_source = (source_root / "infra/scripts/update-redis-acl.py").read_text(encoding="utf-8")
    redis_result = ssh_python(c, root, redis_source, "--apply")
    client = milvus_client(c, admin=True)
    try:
        before = client.describe_user(user_name=c["MILVUS_USER"])["roles"]
        if "admin" not in before:
            client.grant_role(user_name=c["MILVUS_USER"], role_name="admin")
        after = client.describe_user(user_name=c["MILVUS_USER"])["roles"]
        if "admin" not in after:
            raise RuntimeError("Milvus admin role did not become visible")
    finally:
        client.close()
    return {
        "redis": redis_result,
        "milvus": {"before_roles": before, "after_roles": after},
    }


def verify_milvus():
    c = local_settings.settings()
    client = milvus_client(c)
    name = "learning_access_" + uuid4().hex
    checks = {}
    created = False
    try:
        checks["admin_role"] = "admin" in client.describe_user(user_name=c["MILVUS_USER"])["roles"]
        checks["user_role_management_reads"] = (
            c["MILVUS_USER"] in client.list_users() and "admin" in client.list_roles()
        )
        client.create_database(db_name=name)
        created = True
        client.use_database(db_name=name)
        client.create_collection(
            collection_name="synthetic",
            dimension=4,
            metric_type="COSINE",
            consistency_level="Strong",
        )
        client.insert(
            collection_name="synthetic",
            data=[
                {"id": 1, "vector": [1.0, 0.0, 0.0, 0.0], "value": "synthetic-a"},
                {"id": 2, "vector": [0.0, 1.0, 0.0, 0.0], "value": "synthetic-b"},
            ],
        )
        rows = client.query(
            collection_name="synthetic",
            filter="id in [1,2]",
            output_fields=["id", "value"],
            consistency_level="Strong",
        )
        checks["new_database_collection_content"] = {r["id"]: r["value"] for r in rows} == {
            1: "synthetic-a",
            2: "synthetic-b",
        }
        hits = client.search(
            collection_name="synthetic",
            data=[[1.0, 0.0, 0.0, 0.0]],
            limit=1,
            consistency_level="Strong",
        )
        checks["vector_search_content"] = hits[0][0]["id"] == 1
        client.delete(collection_name="synthetic", ids=[2])
        checks["scoped_delete"] = (
            client.query(collection_name="synthetic", filter="id==2", consistency_level="Strong")
            == []
        )
        client.drop_collection(collection_name="synthetic")
        client.use_database(db_name=c["MILVUS_DATABASE"])
        client.drop_database(db_name=name)
        created = False
        checks["scoped_cleanup"] = name not in client.list_databases()
        return checks
    finally:
        if created:
            client.use_database(db_name=name)
            if client.has_collection(collection_name="synthetic"):
                client.drop_collection(collection_name="synthetic")
            client.use_database(db_name=c["MILVUS_DATABASE"])
            client.drop_database(db_name=name)
        client.close()


def verify_all():
    module_path = Path(__file__).with_name("check-redis-acl.py")
    spec = importlib.util.spec_from_file_location("redis_acl_check", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {"redis": module.verify_redis(), "milvus": verify_milvus()}


def main():
    logging.getLogger("pymilvus").setLevel(logging.CRITICAL)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--config-root", type=Path, default=Path(__file__).resolve().parents[2])
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise ValueError("Use a new report file")
    root = args.config_root.resolve()
    local_settings.BASE = root / "infra"
    report = {"status": "FAIL"}
    try:
        if args.apply:
            report["updates"] = apply_policy(root)
        report["checks"] = verify_all()
        if not all(all(checks.values()) for checks in report["checks"].values()):
            raise RuntimeError("Learning access content verification failed")
        report["status"] = "PASS"
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

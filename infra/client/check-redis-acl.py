"""Verify the private learning environment's full Redis application access."""

import argparse
import json
from pathlib import Path
from uuid import uuid4

import redis
import settings as local_settings


def verify_redis():
    c = local_settings.settings()
    app = local_settings.redis_connect()
    other = local_settings.redis_connect()
    base = "learning-acl-check:" + uuid4().hex
    key, zkey = base + ":value", base + ":history"
    checks = {}
    try:
        user = app.acl_getuser(c["REDIS_USER"])
        checks["full_commands_keys_channels"] = (
            user["keys"] == ["~*"]
            and user["channels"] == ["&*"]
            and "+@all" in user["categories"]
            and not any(t.startswith("-") for t in user["commands"])
        )
        checks["existing_password_required"] = (
            "nopass" not in user["flags"] and len(user["passwords"]) == 1
        )
        app.set(key, "1", ex=60)
        checks["arbitrary_key_and_ttl"] = app.get(key) == "1" and 0 < app.ttl(key) <= 60
        script = (
            "local n=redis.call('GET',KEYS[1]); if n==ARGV[1] then "
            "redis.call('SET',KEYS[1],ARGV[2],'EX',60); return 1 end; return 0"
        )
        checks["eval_compare_update"] = app.eval(script, 1, key, "1", "2") == 1
        sha = app.script_load(script)
        checks["script_cache_evalsha"] = (
            app.script_exists(sha) == [True]
            and app.evalsha(sha, 1, key, "1", "stale") == 0
            and app.get(key) == "2"
        )
        read_script = "return redis.call('GET',KEYS[1])"
        read_sha = app.script_load(read_script)
        checks["read_only_scripts"] = (
            app.execute_command("EVAL_RO", read_script, 1, key) == "2"
            and app.execute_command("EVALSHA_RO", read_sha, 1, key) == "2"
        )
        with app.pipeline(transaction=True) as p:
            p.set(key, "3", ex=60)
            p.zadd(zkey, {"early": 1, "late": 3, "middle": 2})
            p.expire(zkey, 60)
            replies = p.execute()
        checks["transaction_and_sorted_history"] = (
            replies == [True, 3, True]
            and app.zrange(zkey, 0, 2) == ["early", "middle", "late"]
            and app.zscore(zkey, "middle") == 2
            and app.zcard(zkey) == 3
        )
        app.zremrangebyscore(zkey, "-inf", 1)
        app.zremrangebyrank(zkey, 0, 0)
        checks["history_trim"] = app.zrange(zkey, 0, 10) == ["late"] and app.zrem(zkey, "late") == 1
        with app.pipeline() as p:
            p.watch(key)
            assert p.get(key) == "3"
            other.set(key, "4", ex=60)
            p.multi()
            p.set(key, "lost-update", ex=60)
            try:
                p.execute()
                checks["watch_conflict"] = False
            except redis.WatchError:
                checks["watch_conflict"] = app.get(key) == "4"
        with app.pipeline() as p:
            p.watch(key)
            p.unwatch()
            p.multi()
            p.set(key, "discarded")
            p.discard()
        checks["unwatch_discard"] = app.get(key) == "4"
        checks["config_access"] = bool(app.config_get("maxmemory"))
        checks["arbitrary_channel"] = (
            app.acl_dryrun(c["REDIS_USER"], "PUBLISH", base + ":channel", "synthetic") == "OK"
        )
        commands = {
            "flushall": ["FLUSHALL"],
            "flushdb": ["FLUSHDB"],
            "config_set": ["CONFIG", "SET", "maxmemory", "67108864"],
            "acl_setuser": ["ACL", "SETUSER", "no-change"],
            "shutdown": ["SHUTDOWN"],
            "script_flush": ["SCRIPT", "FLUSH"],
            "function_flush": ["FUNCTION", "FLUSH"],
        }
        checks["management_commands_permitted"] = all(
            app.acl_dryrun(c["REDIS_USER"], *v) == "OK" for v in commands.values()
        )
        for label, opts in [
            ("unauthenticated", {}),
            ("wrong_password", {"username": c["REDIS_USER"], "password": "invalid-acl-probe"}),
        ]:
            client = redis.Redis(
                host=c["REDIS_HOST"], port=int(c["REDIS_PORT"]), socket_timeout=5, **opts
            )
            try:
                client.ping()
                checks[label + "_rejected"] = False
            except redis.AuthenticationError:
                checks[label + "_rejected"] = True
            finally:
                client.close()
        return checks
    finally:
        app.delete(key, zkey)
        app.close()
        other.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a new report file")
    local_settings.BASE = args.config_root.resolve() / "infra"
    checks = verify_redis()
    report = {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    if report["status"] != "PASS":
        raise RuntimeError("Redis learning access checks failed")


if __name__ == "__main__":
    main()

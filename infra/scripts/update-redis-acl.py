#!/usr/bin/env python3
"""Give the private learning project's Redis app full access, preserving authentication."""

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ACL_FILE = Path("/srv/deephelp-infra/.secrets/redis.acl")
BACKUPS = Path("/srv/deephelp-backup")


def extend_acl(source: bytes) -> bytes:
    """Keep the app's existing password and all other users; replace its permissions."""
    lines = source.decode("utf-8").splitlines(keepends=True)
    matched = []
    for index, line in enumerate(lines):
        tokens = line.split()
        if tokens[:2] == ["user", "deephelp_app"]:
            matched.append(index)
            passwords = [t for t in tokens[2:] if t.startswith("#")]
            if len(passwords) != 1 or len(passwords[0]) != 65:
                raise ValueError("Expected the existing hashed password; do not disable login")
            lines[index] = "user deephelp_app on " + passwords[0] + " ~* &* +@all\n"
    if len(matched) != 1:
        raise ValueError("Expected exactly one existing DeepHelp application ACL")
    return "".join(lines).encode("utf-8")


def load_acl() -> None:
    result = subprocess.run(
        [
            "docker",
            "exec",
            "deephelp-redis",
            "sh",
            "-c",
            "REDISCLI_AUTH=$(cat /run/secrets/redis-admin) "
            "exec redis-cli --user deephelp_admin ACL LOAD",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.stdout.strip() != "OK":
        raise RuntimeError("Redis did not accept the ACL file")


def write_same_inode(data: bytes) -> None:
    # This file is bind-mounted read-only into Redis. Replacing its inode would leave
    # the running container mounted to the old file; rewrite the existing host inode.
    with ACL_FILE.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true")
    group.add_argument("--restore", type=Path)
    args = parser.parse_args()
    original = ACL_FILE.read_bytes()
    proposed = extend_acl(original)
    if args.restore:
        target = args.restore.resolve(strict=True)
        if target.parent != BACKUPS or not target.name.startswith("redis-acl-"):
            raise ValueError("Restore only a recorded Redis ACL backup in the backup directory")
        proposed = target.read_bytes()
    report = {
        "changed": proposed != original,
        "application_permissions": "~* &* +@all" if not args.restore else "restored backup",
        "original_sha256": hashlib.sha256(original).hexdigest(),
        "result_sha256": hashlib.sha256(proposed).hexdigest(),
        "applied": False,
    }
    if (args.apply or args.restore) and proposed != original:
        BACKUPS.mkdir(mode=0o700, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = BACKUPS / ("redis-acl-" + stamp + ".acl")
        # Never expose or change passwords; retain the complete old ACL privately.
        descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        mode, inode = ACL_FILE.stat().st_mode & 0o777, ACL_FILE.stat().st_ino
        try:
            write_same_inode(proposed)
            load_acl()
        except BaseException:
            write_same_inode(original)
            load_acl()
            raise
        assert ACL_FILE.stat().st_ino == inode and ACL_FILE.stat().st_mode & 0o777 == mode
        report.update(applied=True, backup=str(backup))
    elif args.apply or args.restore:
        load_acl()
        report["applied"] = True
    print(json.dumps(report))


if __name__ == "__main__":
    main()

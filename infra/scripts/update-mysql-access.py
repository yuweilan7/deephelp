#!/usr/bin/env python3
"""Give the private learning application's existing MySQL account full access."""

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

POLICY = """GRANT ALL PRIVILEGES ON *.* TO 'deephelp_app'@'%' WITH GRANT OPTION;
ALTER USER 'deephelp_app'@'%' WITH
 MAX_USER_CONNECTIONS 0 MAX_QUERIES_PER_HOUR 0
 MAX_UPDATES_PER_HOUR 0 MAX_CONNECTIONS_PER_HOUR 0;
"""
CONFIG = Path("/srv/deephelp-infra/config/mysql.cnf")
INIT = Path("/srv/deephelp-infra/.secrets/mysql-init.sql")


def sql(text):
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "deephelp-mysql",
            "sh",
            "-c",
            "MYSQL_PWD=$(cat /run/secrets/mysql-root) exec mysql -uroot -NB",
        ],
        input=text,
        text=True,
        capture_output=True,
        timeout=20,
    )
    if result.returncode:
        raise RuntimeError("MySQL policy statement failed")
    return result.stdout


def write_existing(path, data):
    # Preserve the inode of single-file Docker bind mounts and all ownership/mode bits.
    with path.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    grants = sql("SHOW GRANTS FOR 'deephelp_app'@'%';")
    limits = (
        sql(
            "SELECT max_user_connections,max_questions,max_updates,max_connections "
            "FROM mysql.user WHERE User='deephelp_app' AND Host='%';"
        )
        .strip()
        .split("\t")
    )
    if len(limits) != 4 or not all(value.isdecimal() for value in limits):
        raise ValueError("Expected the existing learning account")
    original_global = sql("SELECT @@global.max_connections;").strip()
    restore = "REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'deephelp_app'@'%';\n"
    restore += ";\n".join(grants.strip().splitlines()) + ";\n"
    restore += (
        "ALTER USER 'deephelp_app'@'%' WITH "
        "MAX_USER_CONNECTIONS "
        + limits[0]
        + " MAX_QUERIES_PER_HOUR "
        + limits[1]
        + " MAX_UPDATES_PER_HOUR "
        + limits[2]
        + " MAX_CONNECTIONS_PER_HOUR "
        + limits[3]
        + ";\n"
    )
    old_config, old_init = CONFIG.read_bytes(), INIT.read_bytes()
    new_config = (
        "\n".join(
            "max_connections=151" if line.startswith("max_connections=") else line
            for line in old_config.decode().splitlines()
        )
        + "\n"
    )
    report = {"applied": False, "application_permissions": "ALL ON *.* WITH GRANT OPTION"}
    if args.apply:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")  # noqa: UP017
        backup = Path("/srv/deephelp-backup") / ("learning-mysql-" + stamp)
        backup.mkdir(mode=0o700)
        for name, data in (
            ("grants.sql", restore.encode()),
            ("mysql.cnf", old_config),
            ("mysql-init.sql", old_init),
        ):
            descriptor = os.open(backup / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        try:
            sql(POLICY)
            write_existing(INIT, POLICY.encode())
            write_existing(CONFIG, new_config.encode())
            sql("SET GLOBAL max_connections=151;")
        except BaseException:
            sql(restore + "SET GLOBAL max_connections=" + original_global + ";")
            write_existing(INIT, old_init)
            write_existing(CONFIG, old_config)
            raise
        report.update(applied=True, backup=str(backup))
    report.update(
        account_limits=sql(
            "SELECT max_user_connections,max_questions,max_updates,max_connections "
            "FROM mysql.user WHERE User='deephelp_app' AND Host='%';"
        ).strip(),
        global_connections=sql(
            "SELECT @@global.max_user_connections,@@global.max_connections;"
        ).strip(),
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()

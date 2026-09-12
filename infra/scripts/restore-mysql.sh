#!/usr/bin/env bash
set -euo pipefail
cd /srv/deephelp-infra
[[ $# == 2 ]] || { echo 'Usage: restore-mysql.sh /srv/deephelp-backup/file.sql.gz deephelp_restore_NAME';exit 1; }
file=$(realpath -- "$1");db=$2
[[ "$file" == /srv/deephelp-backup/*.sql.gz && "$db" =~ ^deephelp_restore_[A-Za-z0-9_]+$ ]] || { echo 'Restore only accepts project backup and isolated test database';exit 1; }
bash scripts/disk-guard.sh backup
docker exec deephelp-mysql sh -c 'MYSQL_PWD=$(cat /run/secrets/mysql-root) exec mysql -uroot -e "$1"' sh "CREATE DATABASE \`$db\` CHARACTER SET utf8mb4;"
gzip -dc "$file" | docker exec -i deephelp-mysql sh -c 'MYSQL_PWD=$(cat /run/secrets/mysql-root) exec mysql -uroot --default-character-set=utf8mb4 "$1"' sh "$db"
docker exec deephelp-mysql sh -c 'MYSQL_PWD=$(cat /run/secrets/mysql-root) exec mysql -uroot --default-character-set=utf8mb4 -D "$1" -Nse "SELECT COUNT(*),MIN(content) FROM p00_probe"' sh "$db"

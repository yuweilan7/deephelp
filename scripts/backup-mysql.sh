#!/usr/bin/env bash
set -euo pipefail
cd /srv/deephelp-infra
bash scripts/disk-guard.sh backup
umask 077
file=/srv/deephelp-backup/deephelp-$(date -u +%Y%m%dT%H%M%SZ).sql.gz
docker exec deephelp-mysql sh -c 'MYSQL_PWD=$(cat /run/secrets/mysql-root) exec mysqldump -uroot --single-transaction --routines --triggers --set-gtid-purged=OFF --no-tablespaces --default-character-set=utf8mb4 deephelp' | gzip > "$file"
gzip -t "$file"
printf '%s\n' "$file"

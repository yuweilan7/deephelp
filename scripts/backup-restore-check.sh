#!/usr/bin/env bash
set -euo pipefail
cd /srv/deephelp-infra
file=$(bash scripts/backup-mysql.sh | tail -1)
db=deephelp_restore_p00_$(date -u +%Y%m%d%H%M%S)
bash scripts/restore-mysql.sh "$file" "$db"
printf 'MYSQL_BACKUP_RESTORE PASS\nBackup=%s\nTestDatabase=%s\n' "$file" "$db"

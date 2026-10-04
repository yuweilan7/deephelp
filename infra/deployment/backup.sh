#!/usr/bin/env bash
# Quiesce the sole application writer, then keep DB and required local state together.
set -euo pipefail
umask 077
[[ $# == 0 ]] || { echo 'Usage: backup.sh' >&2; exit 2; }
runtime=$(realpath -e /srv/deephelp-personal)
[[ "$runtime" == /srv/deephelp-personal ]] || { echo 'Unexpected runtime root' >&2; exit 1; }
stamp=$(date -u +%Y%m%dT%H%M%SZ)
destination="/srv/deephelp-backup/personal-$stamp"
mkdir -m 700 -- "$destination"
systemctl stop deephelp-personal@app
trap 'systemctl start deephelp-personal@app' EXIT
bash /srv/deephelp-infra/scripts/backup-mysql.sh > "$destination/mysql-path.txt"
database=$(tail -1 "$destination/mysql-path.txt")
[[ "$database" == /srv/deephelp-backup/*.sql.gz ]] || { echo 'Unexpected DB backup path' >&2; exit 1; }
cp -- "$database" "$destination/database.sql.gz"
tar -czf "$destination/runtime.tar.gz" -C /srv deephelp-personal
readlink -f /opt/deephelp-app/current > "$destination/installed-version.txt"
cd -- "$destination"
sha256sum database.sql.gz runtime.tar.gz installed-version.txt > SHA256SUMS
sha256sum --strict --check SHA256SUMS
printf 'Consistent personal backup: %s\n' "$destination"

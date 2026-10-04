#!/usr/bin/env bash
# All app writers stop before database/state and native platform snapshots.
set -euo pipefail
umask 077
label=${1:?new backup label required}
[[ "$label" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]{1,64}$ ]]
root=/srv/deephelp-modern
[[ $(realpath -e "$root") == "$root" && ! -L /srv && ! -L "$root" ]]
destination="/srv/deephelp-backup/modern-$label"
[[ ! -e "$destination" && ! -L "$destination" ]]
mkdir -m 700 -- "$destination"
mkdir "$root/.backup-lock"
restart() {
  code=$?
  trap - EXIT INT TERM
  if ! bash /opt/deephelp-app/bin/control.sh start; then
    echo 'Application restart failed; preserve backup and lock for recovery' >&2; exit 1
  fi
  rmdir "$root/.backup-lock"
  exit "$code"
}
trap restart EXIT
trap 'exit 130' INT TERM
bash /opt/deephelp-app/bin/control.sh stop
set -a; source "$root/config/runtime.env"; set +a
database=$(/opt/deephelp-app/modern/.venv/bin/python -c 'import json; print(json.load(open("/srv/deephelp-modern/config/postgres.json"))["dbname"])')
[[ "$database" =~ ^deephelp_m25_[a-z0-9_]+$ ]]
connections=$(docker exec deephelp-postgres-protocol psql -X -At -U deephelp_app -d postgres -c "SELECT count(*) FROM pg_stat_activity WHERE datname='$database'")
[[ "$connections" == 0 ]] || { echo 'Unregistered application DB connections remain' >&2; exit 1; }
bash /opt/deephelp-app/bin/postgres-backup.sh "$database" "$destination/application.dump"
learning=${DEEPHELP_LEARNING_DATABASE:-}
if [[ -n "$learning" && "$database" != "$learning" ]]; then
  [[ "$learning" =~ ^deephelp_m25_[a-z0-9_]+$ ]]
  connections=$(docker exec deephelp-postgres-protocol psql -X -At -U deephelp_app -d postgres -c "SELECT count(*) FROM pg_stat_activity WHERE datname='$learning'")
  [[ "$connections" == 0 ]] || { echo 'Learning DB writer/connections remain' >&2; exit 1; }
  bash /opt/deephelp-app/bin/postgres-backup.sh "$learning" "$destination/learning.dump"
fi
bash /srv/deephelp-langfuse/backup.sh "modern-$label"
cp /srv/deephelp-langfuse/backups/"modern-$label".tar.gz "$destination/langfuse.tar.gz"
tar --exclude=deephelp-modern/.backup-lock -czf "$destination/runtime.tar.gz" -C /srv deephelp-modern
readlink -f /opt/deephelp-app/modern > "$destination/installed-version.txt"
tar --exclude='.venv' -czf "$destination/installation.tar.gz" -C /opt/deephelp-app/releases "$(basename "$(cat "$destination/installed-version.txt")")"
cd "$destination"
sha256sum *.dump *.tar.gz installed-version.txt > SHA256SUMS
sha256sum --strict --check SHA256SUMS
echo "Protected complete backup: $destination"

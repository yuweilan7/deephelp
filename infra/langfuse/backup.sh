#!/usr/bin/env bash
# Consistent cold backup of this platform only. Business services remain running.
set -euo pipefail
root=/srv/deephelp-langfuse
[[ $(realpath -e "$root") == "$root" && ! -L /srv && ! -L "$root" ]]
name=${1:?new backup label required}
[[ "$name" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]{1,64}$ ]]
cd "$root"
[[ ! -L backups && ! -L config && ! -L data && ! -L runtime.env ]]
umask 077
archive="$root/backups/$name.tar.gz"
[[ ! -e "$archive" && ! -e "$archive.partial" && ! -e "$archive.sha256" ]]
mkdir .backup-lock
restore_services() {
  local code=$?
  trap - EXIT INT TERM
  if ! sudo docker compose --env-file runtime.env up -d --wait --wait-timeout 180; then
    echo 'Platform restart failed; preserve backup/lock and recover explicitly' >&2
    exit 1
  fi
  rmdir .backup-lock
  exit "$code"
}
trap restore_services EXIT
trap 'exit 130' INT TERM
sudo docker compose --env-file runtime.env stop --timeout 60
sudo tar -czf "$archive.partial" --exclude=config/capacity-worker-stopped \
  compose.yaml clickhouse.xml filer.toml runtime.env config data
sudo chmod 600 "$archive.partial"
sudo chown "$(id -u):$(id -g)" "$archive.partial"
mv "$archive.partial" "$archive"
sha256sum "$archive" > "$archive.sha256"
chmod 600 "$archive.sha256"
echo "Protected cold backup: $archive"

#!/usr/bin/env bash
# Explicit isolated database backup. Run with Docker access on the PG host.
set -euo pipefail
database=${1:?database required}
output=${2:?new absolute dump path required}
[[ "$database" =~ ^deephelp_m25_[a-z0-9_]+$ ]] || { echo 'Isolated M25 database required' >&2; exit 2; }
[[ "$output" = /* && ! -e "$output" && ! -e "$output.partial" ]] || { echo 'New absolute path required' >&2; exit 2; }
umask 077
docker exec deephelp-postgres-protocol pg_dump -U deephelp_app -d "$database" \
  --format=custom --no-owner --no-acl > "$output.partial"
docker exec -i deephelp-postgres-protocol pg_restore --list < "$output.partial" > /dev/null
mv -- "$output.partial" "$output"
sha256sum -- "$output" | cut -d ' ' -f 1 > "$output.sha256"
printf 'PASS: isolated PostgreSQL custom-format backup\n'

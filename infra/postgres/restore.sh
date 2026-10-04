#!/usr/bin/env bash
# Always restore to a new, isolated database. Never overwrite the running facts.
set -euo pipefail
database=${1:?new database required}
dump=${2:?absolute dump path required}
[[ "$database" =~ ^deephelp_m25_[a-z0-9_]+$ ]] || { echo 'Isolated M25 database required' >&2; exit 2; }
[[ "$dump" = /* && -f "$dump" && -f "$dump.sha256" ]] || { echo 'Dump and checksum required' >&2; exit 2; }
expected=$(cat -- "$dump.sha256")
actual=$(sha256sum -- "$dump" | cut -d ' ' -f 1)
[[ "$expected" =~ ^[0-9a-f]{64}$ && "$expected" = "$actual" ]] || { echo 'Dump checksum differs' >&2; exit 2; }
# CREATE fails if it exists. Restoration is atomic; failure leaves a new empty DB for diagnosis.
docker exec deephelp-postgres-protocol psql -X -v ON_ERROR_STOP=1 -U deephelp_app -d postgres \
  -c "CREATE DATABASE $database"
docker exec -i deephelp-postgres-protocol pg_restore -U deephelp_app -d "$database" \
  --exit-on-error --single-transaction --no-owner --no-acl < "$dump"
printf 'PASS: restored into new isolated database; run snapshot verification and fact queries\n'

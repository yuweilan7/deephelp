#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath -e "$0")")"
compose=(sudo docker compose --env-file runtime.env)
"${compose[@]}" ps
"${compose[@]}" exec -T clickhouse sh -c 'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --query "SELECT version(); SHOW TABLES"'
"${compose[@]}" exec -T redis sh -c 'export REDISCLI_AUTH="$REDIS_PASSWORD"; redis-cli CONFIG GET maxmemory-policy appendonly appendfsync; redis-cli INFO persistence; redis-cli DBSIZE'
"${compose[@]}" exec -T postgres psql -U langfuse -d langfuse -c 'SELECT version()'
free -m
df -h .

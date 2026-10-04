#!/usr/bin/env bash
set -euo pipefail
cd /srv/deephelp-infra
bash scripts/doctor.sh
docker exec deephelp-postgres-protocol psql -X -At -U deephelp_app -d postgres -c 'SELECT version(),1'
docker exec deephelp-redis sh -c 'REDISCLI_AUTH=$(cat /run/secrets/redis-app) exec redis-cli --user deephelp_app ping'
echo 'Milvus functional acceptance: run client/test-connections.py on developer PC through SSH tunnel.'

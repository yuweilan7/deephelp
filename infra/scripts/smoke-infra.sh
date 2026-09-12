#!/usr/bin/env bash
set -euo pipefail
cd /srv/deephelp-infra
bash scripts/doctor.sh
docker exec deephelp-mysql sh -c 'MYSQL_PWD=$(cat /run/secrets/mysql-app) exec mysql -h127.0.0.1 -udeephelp_app -Ddeephelp -Nse "SELECT VERSION(),DATABASE(),1"'
docker exec deephelp-redis sh -c 'REDISCLI_AUTH=$(cat /run/secrets/redis-app) exec redis-cli --user deephelp_app ping'
echo 'Milvus functional acceptance: run client/test-connections.py on developer PC through SSH tunnel.'

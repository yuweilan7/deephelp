#!/usr/bin/env bash
set -euo pipefail
cd /srv/deephelp-infra
docker compose --env-file .env --env-file .env.secret "$@"

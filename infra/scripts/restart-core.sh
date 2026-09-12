#!/usr/bin/env bash
set -euo pipefail
cd /srv/deephelp-infra
bash scripts/compose.sh restart mysql redis
bash scripts/compose.sh up -d --wait --wait-timeout 240 mysql redis

#!/usr/bin/env bash
set -euo pipefail
cd /srv/deephelp-infra
bash scripts/compose.sh stop -t 60 mysql redis

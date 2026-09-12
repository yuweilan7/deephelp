#!/usr/bin/env bash
set -euo pipefail
cd /srv/deephelp-infra
bash scripts/compose.sh stop -t 120 milvus

#!/usr/bin/env bash
set -euo pipefail
cd /srv/deephelp-infra
bash scripts/start-core.sh
bash scripts/start-milvus.sh

#!/usr/bin/env bash
set -euo pipefail
cd /srv/deephelp-infra
bash scripts/disk-guard.sh check
bash scripts/compose.sh up -d --wait --wait-timeout 300 milvus

#!/usr/bin/env bash
set -euo pipefail
umask 077
version_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
: "${DEEPHELP_RUNTIME_ROOT:?Set the persistent configuration root}"
: "${DEEPHELP_STATE_ROOT:?Set the persistent state root}"
cd -- "$DEEPHELP_RUNTIME_ROOT"
python="$version_dir/.venv/bin/python"
state="$DEEPHELP_STATE_ROOT"
case "${1:-}" in
  rights)
    exec "$python" -m deephelp_tools.demo.synthetic_rights --port 8015 --key "$state/rights.key" ;;
  business)
    exec "$python" -m deephelp_services.business serve --credentials "$state/business.json" --port 8011 ;;
  retrieval)
    exec "$python" -m deephelp_services.retrieval serve --credentials "$state/retrieval.json" --port 8012 \
      --providers modules/deephelp-app/providers.example.json --auth "$state/auth.json" \
      --pointer "$state/pointer.json" --corpus "$DEEPHELP_CORPUS" ;;
  app)
    : "${DEEPHELP_RELEASE_CHANNEL:?Set the authoritative personal release channel}"
    exec "$python" -m deephelp_tools.learning.workbench serve --port 8000 \
      --business-mcp-url http://127.0.0.1:8011/mcp --service-credentials "$state/business.json" \
      --retrieval-mcp-url http://127.0.0.1:8012/mcp --retrieval-credentials "$state/retrieval.json" \
      --providers modules/deephelp-app/providers.example.json --pointer "$state/pointer.json" \
      --fasttext-pointer "$state/fasttext.json" --corpus "$DEEPHELP_CORPUS" \
      --auth "$state/auth.json" --budget-state "$state/budget.json" --trace-path "$state/trace.jsonl" \
      --release-channel "$DEEPHELP_RELEASE_CHANNEL" ;;
  *) echo 'Usage: start.sh app|business|retrieval|rights' >&2; exit 2 ;;
esac

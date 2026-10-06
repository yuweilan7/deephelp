#!/usr/bin/env bash
set -euo pipefail
umask 077
version_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
: "${DEEPHELP_RUNTIME_ROOT:?Set the persistent configuration root}"
: "${DEEPHELP_STATE_ROOT:?Set the persistent state root}"
cd -- "$DEEPHELP_RUNTIME_ROOT"
python="$version_dir/.venv/bin/python"
state="$DEEPHELP_STATE_ROOT"
: "${DEEPHELP_POSTGRES_CONFIG:?Set the application PostgreSQL configuration}"
: "${DEEPHELP_LANGFUSE_CONFIG:?Set the native observation configuration}"
app_port=${DEEPHELP_APP_PORT:-8000}
business_port=${DEEPHELP_BUSINESS_PORT:-8011}
retrieval_port=${DEEPHELP_RETRIEVAL_PORT:-8012}
rights_port=${DEEPHELP_LEARNING_RIGHTS_PORT:-8015}
providers=${DEEPHELP_PROVIDERS:-modules/deephelp-app/providers.example.json}
case "${1:-}" in
  rights)
    unset DEEPHELP_METRICS_PORT
    exec "$python" -m deephelp_tools.demo.synthetic_rights --port "$rights_port" --key "$state/rights.key" ;;
  business)
    export DEEPHELP_METRICS_PORT=${DEEPHELP_BUSINESS_METRICS_PORT:-9465}
    exec "$python" -m deephelp_services.business serve --credentials "$state/business.json" --port "$business_port" \
      --catalog-version "${DEEPHELP_BUSINESS_CATALOG:-complaints-v1}" ;;
  retrieval)
    export DEEPHELP_METRICS_PORT=${DEEPHELP_RETRIEVAL_METRICS_PORT:-9466}
    exec "$python" -m deephelp_services.retrieval serve --credentials "$state/retrieval.json" --port "$retrieval_port" \
      --providers "$providers" --auth "$state/auth.json" \
      --pointer "$state/pointer.json" --corpus "$DEEPHELP_CORPUS" ;;
  app)
    : "${DEEPHELP_PERSONAL_IDENTITY:?Set the explicit existing personal identity JSON}"
    export DEEPHELP_METRICS_PORT=${DEEPHELP_APP_METRICS_PORT:-9464}
    release_args=()
    if [[ -n "${DEEPHELP_RELEASE_CHANNEL:-}" ]]; then
      release_args=(--release-channel "$DEEPHELP_RELEASE_CHANNEL")
    elif [[ "${DEEPHELP_ISOLATED_REHEARSAL:-}" != 1 ]]; then
      echo 'An accepted complete release channel is required outside isolated rehearsal' >&2; exit 2
    fi
    exec "$python" -m deephelp_tools.learning.workbench serve --port "$app_port" \
      --business-mcp-url "http://127.0.0.1:$business_port/mcp" --service-credentials "$state/business.json" \
      --retrieval-mcp-url "http://127.0.0.1:$retrieval_port/mcp" --retrieval-credentials "$state/retrieval.json" \
      --providers "$providers" --pointer "$state/pointer.json" \
      --fasttext-pointer "$state/fasttext.json" --corpus "$DEEPHELP_CORPUS" \
      --auth "$state/auth.json" --personal-identity "$DEEPHELP_PERSONAL_IDENTITY" \
      --budget-state "$state/budget.json" --trace-path "$state/trace.jsonl" \
      "${release_args[@]}" ;;
  *) echo 'Usage: start.sh app|business|retrieval|rights' >&2; exit 2 ;;
esac

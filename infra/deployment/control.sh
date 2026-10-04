#!/usr/bin/env bash
# Manage every registered writer, including process groups and learning subprocesses.
set -euo pipefail
units=(deephelp-modern@app deephelp-modern@business deephelp-modern@retrieval deephelp-modern@rights)
case "${1:-}" in
  stop)
    systemctl stop "${units[@]}"
    for unit in "${units[@]}"; do
      [[ $(systemctl show "$unit" -p MainPID --value) == 0 ]]
      [[ $(systemctl show "$unit" -p ActiveState --value) != active ]]
    done ;;
  start)
    systemctl start deephelp-modern@rights deephelp-modern@business deephelp-modern@retrieval deephelp-modern@app ;;
  health)
    for unit in "${units[@]}"; do systemctl is-active --quiet "$unit"; done
    root=${DEEPHELP_RUNTIME_ROOT:-/srv/deephelp-modern}
    # Environment files are protected administrator configuration, never browser input.
    set -a; source "$root/config/runtime.env"; set +a
    curl -fsS --max-time 5 "http://127.0.0.1:${DEEPHELP_APP_PORT:-8000}/health"
    for port in "${DEEPHELP_APP_METRICS_PORT:-9464}" "${DEEPHELP_BUSINESS_METRICS_PORT:-9465}" "${DEEPHELP_RETRIEVAL_METRICS_PORT:-9466}"; do
      curl -fsS --max-time 5 "http://127.0.0.1:$port/metrics" | grep '^target_info' >/dev/null
    done ;;
  *) echo 'Usage: control.sh start|stop|health' >&2; exit 2 ;;
esac

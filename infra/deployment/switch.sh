#!/usr/bin/env bash
# Switch installed application files; never mutate business/release state or replay a request.
set -euo pipefail
[[ $# == 1 && "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$ ]] || {
  echo 'Usage: switch.sh INSTALLED_VERSION' >&2; exit 2;
}
base=/opt/deephelp-app
target=$(realpath -e -- "$base/releases/$1")
[[ "$target" == "$base/releases/$1" && -x "$target/start.sh" && -x "$target/.venv/bin/python" ]] || {
  echo 'Target must be an installed version inside releases' >&2; exit 1;
}
cd -- "$target"
sha256sum --strict --check SHA256SUMS
systemctl stop deephelp-personal@app deephelp-personal@retrieval deephelp-personal@business
temporary="$base/.current.$$"
trap 'rm -f -- "$temporary"' EXIT
ln -s -- "$target" "$temporary"
mv -Tf -- "$temporary" "$base/current"
systemctl start deephelp-personal@business deephelp-personal@retrieval deephelp-personal@app
for attempt in {1..20}; do
  if curl -fsS --max-time 2 http://127.0.0.1:8000/health >/dev/null 2>&1; then
    printf 'Serving installed version %s\n' "$1"; exit 0
  fi
  sleep 1
done
echo 'Startup did not become healthy; inspect journal and explicitly restore the previous version' >&2
exit 1

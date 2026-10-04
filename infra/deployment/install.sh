#!/usr/bin/env bash
# Install one verified immutable bundle. Configuration and assets are delivered separately.
set -euo pipefail
umask 077
[[ $# == 2 && "$2" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$ ]] || {
  echo 'Usage: install.sh ABSOLUTE_BUNDLE_DIR VERSION' >&2; exit 2;
}
bundle=$(realpath -e -- "$1")
base=/opt/deephelp-app/releases
version=$2
target="$base/$version"
[[ ! -e "$target" ]] || { echo 'Version already exists; never overwrite it' >&2; exit 1; }
cd -- "$bundle"
sha256sum --strict --check SHA256SUMS
[[ $(uv --version) == 'uv 0.12.13'* ]] || { echo 'uv 0.12.13 required' >&2; exit 1; }
python=$(uv python find 3.14.7)
"$python" -c 'import sys; assert sys.version_info[:3] == (3,14,7)'
mkdir -p -- "$target"
cp -- requirements.lock start.sh SHA256SUMS "$target/"
mkdir -- "$target/wheels"
cp -- wheels/*.whl "$target/wheels/"
uv venv --python "$python" "$target/.venv"
source_args=()
if [[ -n "${DEEPHELP_WHEELHOUSE:-}" ]]; then
  source_args=(--no-index --find-links "$(realpath -e -- "$DEEPHELP_WHEELHOUSE")")
fi
uv pip sync --python "$target/.venv/bin/python" --require-hashes \
  "${source_args[@]}" "$target/requirements.lock"
uv pip install --python "$target/.venv/bin/python" --no-deps "$target"/wheels/*.whl
"$target/.venv/bin/python" -c 'import sys,fasttext,deephelp_app,deephelp_services,deephelp_tools,opentelemetry.sdk.trace; assert sys.version_info[:3] == (3,14,7)'
chmod 700 -- "$target/start.sh"
printf 'Installed %s. Complete release and browser acceptance are separate gates.\n' "$version"

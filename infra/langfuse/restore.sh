#!/usr/bin/env bash
# Fresh isolated destination, same locked images, alternate loopback ports.
set -euo pipefail
archive=$(realpath -e "${1:?protected archive required}")
destination=${2:?fresh absolute restore directory required}
[[ "$archive" == /srv/deephelp-langfuse/backups/*.tar.gz && ! -L /srv ]]
[[ "$destination" =~ ^/srv/deephelp-langfuse-restore-[a-z0-9-]{1,40}$ ]]
[[ ! -e "$destination" && ! -L "$destination" ]]
[[ ! -L /srv/deephelp-langfuse && ! -L /srv/deephelp-langfuse/backups ]]
sha256sum -c "$archive.sha256"
umask 077
sudo mkdir -m 700 "$destination"
# tar data filter rejects escaping paths/links; CH's relative in-root metadata links remain valid.
sudo /opt/deephelp-app/current/.venv/bin/python - "$archive" "$destination" <<'PY'
import sys, tarfile
with tarfile.open(sys.argv[1]) as archive:
    archive.extractall(sys.argv[2], filter='data')
PY
sudo chown "$(id -u):$(id -g)" "$destination" "$destination/runtime.env" "$destination/config"
sudo chown -R "$(id -u):$(id -g)" "$destination/config"
sudo chown -R 101:101 "$destination/data/clickhouse" "$destination/data/clickhouse-logs"
sudo chown -R 999:999 "$destination/data/postgres"
cd "$destination"
python3 - "$destination" <<'PY'
import json,sys
from pathlib import Path
root=Path(sys.argv[1]); path=root/'runtime.env'
env=dict(line.split('=',1) for line in path.read_text().splitlines() if '=' in line)
env.update(LF_PROJECT=root.name,LF_WEB_PORT='13310',LF_S3_PORT='18343',LF_WEB_URL='http://127.0.0.1:13310')
path.write_text(''.join(f'{key}={value}\n' for key,value in env.items()))
probe=root/'config/probe.json'; config=json.loads(probe.read_text())
config.update(host=env['LF_WEB_URL'],s3_endpoint='http://127.0.0.1:18343')
probe.write_text(json.dumps(config));probe.chmod(0o600)
PY
sudo docker compose --env-file runtime.env config --quiet
sudo docker compose --env-file runtime.env up -d --wait --wait-timeout 180
echo "Restore running in $destination; verify content before removing it"

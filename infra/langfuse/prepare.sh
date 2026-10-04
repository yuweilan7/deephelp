#!/usr/bin/env bash
# Run as the owner of an explicitly isolated /srv directory after copying this directory.
set -euo pipefail
root=$(realpath -e "${1:?absolute isolated root required}")
case "$root" in /srv/deephelp-langfuse) ;; *) echo 'Use the dedicated platform directory' >&2; exit 2;; esac
[[ "$1" == "$root" && ! -L "$root" && ! -L /srv ]]
cd "$root"
umask 077
mkdir -p config data/{postgres,redis,clickhouse,clickhouse-logs,seaweedfs/master,seaweedfs/filer} backups
for path in config data data/postgres data/redis data/clickhouse data/clickhouse-logs \
  data/seaweedfs data/seaweedfs/master data/seaweedfs/filer; do
  [[ $(realpath -e "$path") == "$root/$path" && ! -L "$path" ]]
done
if [[ ! -f runtime.env ]]; then
  python3 - <<'PY'
import json, secrets
from pathlib import Path
keys = ('POSTGRES_PASSWORD','CLICKHOUSE_PASSWORD','REDIS_PASSWORD','S3_ACCESS_KEY','S3_SECRET_KEY',
        'SALT','ENCRYPTION_KEY','NEXTAUTH_SECRET','LF_PUBLIC_KEY','LF_SECRET_KEY','LF_USER_PASSWORD')
values = {key: secrets.token_hex(32) for key in keys}
values['LF_PUBLIC_KEY'] = 'pk-lf-' + values['LF_PUBLIC_KEY']
values['LF_SECRET_KEY'] = 'sk-lf-' + values['LF_SECRET_KEY']
values.update(LF_PROJECT='deephelp-langfuse', LF_WEB_PORT='13300', LF_S3_PORT='18333',
              LF_WEB_URL='http://127.0.0.1:13300', LF_USER_EMAIL='learner@example.invalid')
Path('runtime.env').write_text(''.join(f'{key}={value}\n' for key,value in values.items()))
identity = {'identities':[{'name':'langfuse','credentials':[{
    'accessKey':values['S3_ACCESS_KEY'],'secretKey':values['S3_SECRET_KEY']}],
    'actions':['Admin','Read','Write','List','Tagging']}]}
Path('config/s3.json').write_text(json.dumps(identity))
config = dict(host=values['LF_WEB_URL'], public_key=values['LF_PUBLIC_KEY'],
              secret_key=values['LF_SECRET_KEY'], s3_endpoint='http://127.0.0.1:18333',
              s3_access_key=values['S3_ACCESS_KEY'], s3_secret_key=values['S3_SECRET_KEY'],
              bucket='langfuse')
Path('config/probe.json').write_text(json.dumps(config))
PY
fi
[[ -s runtime.env && -s config/s3.json && -s config/probe.json ]]
chmod 600 runtime.env config/s3.json config/probe.json
sudo chown -R 101:101 data/clickhouse data/clickhouse-logs
sudo chown -R 999:999 data/postgres
sudo docker compose --env-file runtime.env config --quiet

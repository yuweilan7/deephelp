#!/usr/bin/env bash
# Fresh PG database and runtime root only. Platform restore remains isolated too.
set -euo pipefail
umask 077
backup=$(realpath -e "${1:?protected modern backup directory required}")
database=${2:?new isolated database required}
destination=${3:?new isolated runtime root required}
[[ "$backup" == /srv/deephelp-backup/modern-* && ! -L /srv && ! -L /srv/deephelp-backup ]]
[[ "$database" =~ ^deephelp_m25_m30_restore_[a-z0-9_]{1,32}$ ]]
[[ "$destination" =~ ^/srv/deephelp-modern-restore-[a-z0-9-]{1,32}$ ]]
[[ ! -e "$destination" && ! -L "$destination" ]]
cd "$backup"
sha256sum --strict --check SHA256SUMS
bash /opt/deephelp-app/bin/postgres-restore.sh "$database" "$backup/application.dump"
mkdir -m 700 "$destination"
/opt/deephelp-app/modern/.venv/bin/python - "$backup/runtime.tar.gz" "$destination" "$database" <<'PY'
import json, pathlib, sys, tarfile
archive, target, database = sys.argv[1:]
root = pathlib.Path(target)
with tarfile.open(archive) as source:
    source.extractall(root, filter='data')
saved = root / 'deephelp-modern'
# Keep immutable manifests/artifacts unchanged; adjust only deployment controls.
for path in (saved / 'config').glob('*'):
    if path.suffix == '.env':
        path.write_text(path.read_text().replace('/srv/deephelp-modern', str(saved)))
catalog = saved / 'config/learning-catalog.json'
if catalog.exists():
    catalog.write_text(catalog.read_text().replace('/srv/deephelp-modern', str(saved)))
mapping = saved / 'config/artifact-map.json'
rows = json.loads(mapping.read_text())
for row in rows:
    row['target'] = row['target'].replace('/srv/deephelp-modern', str(saved))
mapping.write_text(json.dumps(rows))
pg = saved / 'config/postgres.json'
value = json.loads(pg.read_text()); value['dbname'] = database
pg.write_text(json.dumps(value))
PY
echo 'Restore complete; verify full PG hashes, history, approvals, release and isolated platform content before use'

#!/usr/bin/env bash
set -euo pipefail
cd /srv/deephelp-infra
for ref in mysql:8.4.11 redis:8.2.8-alpine milvusdb/milvus:v2.6.23; do
  bash scripts/disk-guard.sh pull
  timeout 1200 docker pull "$ref"
done
python3 - <<'PY'
import json,subprocess,pathlib
refs={'MYSQL_IMAGE':'mysql:8.4.11','REDIS_IMAGE':'redis:8.2.8-alpine','MILVUS_IMAGE':'milvusdb/milvus:v2.6.23'}
r={};lines=[]
for key,tag in refs.items():
    d=json.loads(subprocess.check_output(['docker','image','inspect',tag]))[0]
    digest=d['RepoDigests'][0].split('@')[1]
    lines.append(key+'='+tag+'@'+digest)
    r[key]={'tag':tag,'digest':digest,'image_id':d['Id'],'size':d['Size'],'created':d['Created']}
pathlib.Path('.env').write_text('\n'.join(lines)+'\n')
pathlib.Path('reports/images.lock.json').write_text(json.dumps(r,indent=2))
print('IMAGES_LOCKED',json.dumps(r))
PY

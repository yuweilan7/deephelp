#!/usr/bin/env bash
set -euo pipefail
cd /srv/deephelp-infra
bash scripts/disk-guard.sh check
python3 - <<'PY'
import json,subprocess
for name,ports in [('mysql',['3306/tcp']),('redis',['6379/tcp']),('milvus',['19530/tcp','9091/tcp'])]:
 d=json.loads(subprocess.check_output(['docker','inspect','deephelp-'+name]))[0]
 assert d['State']['Running'] and d['State']['Health']['Status']=='healthy',name+' not healthy'
 assert not d['State']['OOMKilled'],name+' OOM'
 for port in ports:
  p=d['NetworkSettings']['Ports'][port];assert p and all(x['HostIp']=='127.0.0.1' for x in p),(name,port,p)
 log=d['HostConfig']['LogConfig'];assert log['Config'].get('max-size')=='10m' and log['Config'].get('max-file')=='3'
 print(name,'HEALTHY','memory cap',d['HostConfig']['Memory'],'CPU',d['HostConfig']['NanoCpus']/1e9,'loopback-only; bounded logs')
PY
curl -fsS http://127.0.0.1:9091/healthz
echo
systemctl is-enabled deephelp-infra.service

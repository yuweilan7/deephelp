#!/usr/bin/env bash
set -euo pipefail
python3 - "${1:-check}" <<'PY'
import shutil,subprocess,sys
GiB=1024**3
free=shutil.disk_usage('/').free
print(f'ROOT_FREE_GIB={free/GiB:.2f} DISK_LIMIT_TYPE=SOFT_GUARD')
if free < 8*GiB: print('CRITICAL');sys.exit(2)
if free < 10*GiB: print('STOP NEW PULL/IMPORT/BUILD/BACKUP');sys.exit(2)
if free < 12*GiB: print('WARNING: below target headroom')
budgets={'/srv/deephelp-data/milvus':6*GiB,'/srv/deephelp-data/mysql':2*GiB,'/srv/deephelp-data/redis':256*1024**2,'/srv/deephelp-backup':2*GiB,'/srv/deephelp-infra':GiB}
import os
for path,budget in budgets.items():
    if os.path.exists(path):
        used=int(subprocess.check_output(['du','-s','-B1',path]).split()[0])
        if used>budget*.8: print(f'WARNING {path}: {used} / {budget} bytes')
        if used>=budget and sys.argv[1]!='check': sys.exit(f'STOP {path}: soft budget exceeded')
used=sum(int(subprocess.check_output(['du','-s','-B1',p]).split()[0]) for p in ['/var/lib/docker','/var/lib/containerd'] if os.path.exists(p))
print(f'DOCKER_CONTENT_GIB={used/GiB:.2f}')
if sys.argv[1] in ('pull','build') and (used>=8*GiB or free < 14*GiB): sys.exit('STOP: image admission budget/headroom')
PY

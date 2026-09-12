"""Project-only provisioning, run with sudo after baseline and fixed image pulls."""
import os,pathlib,secrets,subprocess,json
b=pathlib.Path('/srv/deephelp-infra');os.chdir(b)
assert (b/'reports/infra_before.json').exists()
assert (b/'.env').exists(),'Pull and lock images first'
def write(p,s,mode=0o750):
    p=pathlib.Path(p);p.write_text(s);p.chmod(mode)
for path in ['/srv/deephelp','/srv/deephelp-data/mysql','/srv/deephelp-data/redis','/srv/deephelp-data/milvus','/srv/deephelp-backup']:
    pathlib.Path(path).mkdir(parents=True,exist_ok=True)
secret=b/'.env.secret'
if not secret.exists():
    values={k:secrets.token_hex(24) for k in ['MYSQL_ROOT_PASSWORD','MYSQL_PASSWORD','REDIS_PASSWORD','REDIS_ADMIN_PASSWORD','MILVUS_ROOT_PASSWORD','MILVUS_PASSWORD','MILVUS_BOOTSTRAP_PASSWORD']}
    fd=os.open(secret,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write(''.join(k+'='+v+'\n' for k,v in values.items()))
values=dict(line.split('=',1) for line in secret.read_text().splitlines() if '=' in line)
secret.chmod(0o600);os.chown(secret,1000,1000)
s=b/'.secrets';s.mkdir(exist_ok=True);s.chmod(0o700)
for svc in ['mysql','redis']:
    image=dict(l.split('=',1) for l in (b/'.env').read_text().splitlines())[svc.upper()+'_IMAGE']
    uid=int(subprocess.check_output(['docker','run','--rm','--network','none','--entrypoint','id',image,'-u',svc]))
    for suffix,key in [('root','MYSQL_ROOT_PASSWORD'),('app','MYSQL_PASSWORD')] if svc=='mysql' else [('app','REDIS_PASSWORD'),('admin','REDIS_ADMIN_PASSWORD')]:
        p=s/(svc+'-'+suffix);write(p,values[key]+'\n',0o400);os.chown(p,uid,uid)
    os.chown('/srv/deephelp-data/'+svc,uid,uid)
    if svc=='redis':redis_uid=uid
write(s/'mysql-init.sql',"REVOKE ALL PRIVILEGES ON deephelp.* FROM 'deephelp_app'@'%';\nGRANT SELECT,INSERT,UPDATE,DELETE,CREATE,ALTER,INDEX,DROP,REFERENCES,CREATE TEMPORARY TABLES ON deephelp.* TO 'deephelp_app'@'%';\nALTER USER 'deephelp_app'@'%' WITH MAX_USER_CONNECTIONS 16;\n",0o444)
import hashlib
h=lambda v:hashlib.sha256(v.encode()).hexdigest()
acl='user default off\nuser deephelp_admin on #'+h(values['REDIS_ADMIN_PASSWORD'])+' ~* &* +@all\nuser deephelp_app on #'+h(values['REDIS_PASSWORD'])+' ~deephelp:* +ping +get +set +del +unlink +exists +expire +pexpire +ttl +pttl +mget +mset +incr +decr +hget +hset +hgetall +hdel\n'
write(s/'redis.acl',acl,0o400);os.chown(s/'redis.acl',redis_uid,redis_uid)
actions={'start-core':'bash scripts/compose.sh up -d --wait --wait-timeout 240 mysql redis','stop-core':'bash scripts/compose.sh stop -t 60 mysql redis','restart-core':'bash scripts/compose.sh restart mysql redis\nbash scripts/compose.sh up -d --wait --wait-timeout 240 mysql redis','start-milvus':'bash scripts/disk-guard.sh check\nbash scripts/compose.sh up -d --wait --wait-timeout 300 milvus','stop-milvus':'bash scripts/compose.sh stop -t 120 milvus','start-all':'bash scripts/start-core.sh\nbash scripts/start-milvus.sh'}
for name,cmd in actions.items():write(b/'scripts'/(name+'.sh'),'#!/usr/bin/env bash\nset -euo pipefail\ncd /srv/deephelp-infra\n'+cmd+'\n')
write('/etc/systemd/system/deephelp-infra.service','[Unit]\nDescription=DeepHelp experimental middleware\nRequires=docker.service\nAfter=docker.service network-online.target\nWants=network-online.target\nStartLimitIntervalSec=900\nStartLimitBurst=3\n[Service]\nType=oneshot\nRemainAfterExit=yes\nWorkingDirectory=/srv/deephelp-infra\nExecStart=/usr/bin/bash /srv/deephelp-infra/scripts/start-all.sh\nExecStop=/usr/bin/bash /srv/deephelp-infra/scripts/compose.sh stop -t 120\nTimeoutStartSec=600\nTimeoutStopSec=180\nRestart=on-failure\nRestartSec=60\n[Install]\nWantedBy=multi-user.target\n',0o644)
for p in (b/'scripts').glob('*.sh'):p.chmod(0o750)
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','deephelp-infra.service'],check=True)
print('BOOTSTRAP_CONFIGURED; secret path:',secret)

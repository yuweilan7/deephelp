import csv,datetime,hashlib,json,os,pathlib,subprocess
b=pathlib.Path('/srv/deephelp-infra');os.chdir(b)
def command(args):return subprocess.check_output(args,text=True).strip()
info=json.loads(command(['docker','info','--format','{{json .}}']))
r={'captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'docker':{k:info.get(k) for k in ['ServerVersion','Driver','DriverStatus','DockerRootDir','CgroupVersion','MemTotal','NCPU','KernelVersion','OperatingSystem','Architecture']},'containers':{},'memory':command(['free','-b']),'disk':command(['df','-B1','/']),'disk_usage':command(['du','-s','-B1','/srv/deephelp-data/mysql','/srv/deephelp-data/redis','/srv/deephelp-data/milvus','/srv/deephelp-backup','/srv/deephelp-infra','/var/lib/docker','/var/lib/containerd']),'listeners':command(['ss','-lntp'])}
events=command(['docker','events','--since','45m','--until',datetime.datetime.now(datetime.timezone.utc).isoformat(),'--filter','label=com.docker.compose.project=deephelp','--format','{{json .}}'])
r['lifecycle_events']=[json.loads(line) for line in events.splitlines() if line]
for name in ['mysql','redis','milvus']:
 d=json.loads(command(['docker','inspect','deephelp-'+name]))[0]
 r['containers'][name]={'state':d['State'],'restart_count':d['RestartCount'],'ports':d['NetworkSettings']['Ports'],'mounts':d['Mounts'],'limits':{k:d['HostConfig'][k] for k in ['Memory','MemorySwap','NanoCpus','LogConfig','RestartPolicy']},'image':d['Config']['Image']}
(b/'reports/infra_after.json').write_text(json.dumps(r,indent=2))
before=json.loads((b/'reports/infra_before.json').read_text())
lock={'os':r['docker']['OperatingSystem'],'kernel':r['docker']['KernelVersion'],'cpu':before['lscpu']['stdout'],'simd':['sse4_2','avx','avx2'],'memory_bytes':info['MemTotal'],'filesystem':before['df -hT']['stdout'],'docker':r['docker'],'compose_version':command(['docker','compose','version','--short']),'containerd_image_store':True,'containerd_root':'/var/lib/containerd','images':json.loads((b/'reports/images.lock.json').read_text()),'milvus':{'server_version':'2.6.23','pymilvus_recommended':'2.6.17','release_url':'https://github.com/milvus-io/milvus/releases/tag/v2.6.23','release_commit':'bfa1bc3','script_source':'https://raw.githubusercontent.com/milvus-io/milvus/v2.6.23/scripts/standalone_embed.sh','script_sha256':hashlib.sha256((b/'vendor/standalone_embed-v2.6.23.sh').read_bytes()).hexdigest(),'original_script_tag_mismatch':'v2.6.22; corrected to v2.6.23 in compose','embedded_etcd_config':(b/'config/embedEtcd.yaml').read_text(),'wal':'woodpecker local'},'mysql':{'version':command(['docker','exec','deephelp-mysql','mysql','--version']),'release_url':'https://dev.mysql.com/doc/relnotes/mysql/8.4/en/news-8-4-11.html','selection_reason':'8.4.12 release notes exist but official Docker tag absent; 8.4.11 official tag verified'},'redis':{'version':command(['docker','exec','deephelp-redis','redis-server','--version']),'release_url':'https://github.com/redis/redis/releases/tag/8.2.8'},'network_and_resource_configuration':r['containers'],'ssh':{'port':22,'user':'ubuntu','host':'134.175.142.187','dns':None,'instance_id':'lhins-56mgod8p','host_fingerprint':'SHA256:a8ekBn8a1SBNtDdUAa7Qz3L8fV7zklTo5KPPXDYOLQU'},'disk_limit_type':'SOFT_GUARD'}
lock['milvus']['release_commit']='bfa1bc34f93df2ccd20fa7c060463c6c6812d355'
(b/'reports/environment.lock.json').write_text(json.dumps(lock,indent=2))
with (b/'reports/resource_caps.csv').open('w',newline='') as f:
 w=csv.writer(f);w.writerow(['service','memory_limit_bytes','swap_total_limit_bytes','cpu_limit','disk_soft_budget_bytes'])
 for name,budget in [('milvus',6*1024**3),('mysql',2*1024**3),('redis',256*1024**2)]:
  c=r['containers'][name]['limits'];w.writerow([name,c['Memory'],c['MemorySwap'],c['NanoCpus']/1e9,budget])
print('AFTER_AND_LOCK_WRITTEN')

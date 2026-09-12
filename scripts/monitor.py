"""Bounded 12-minute observation; no service mutation."""
import datetime,json,pathlib,subprocess,time,os
b=pathlib.Path('/srv/deephelp-infra/reports')
end=time.monotonic()+720
with (b/'stability.jsonl').open('a',buffering=1) as f:
    while True:
        r={'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'uptime':float(pathlib.Path('/proc/uptime').read_text().split()[0])}
        r['meminfo']={l.split(':')[0]:int(l.split()[1])*1024 for l in pathlib.Path('/proc/meminfo').read_text().splitlines() if l.split(':')[0] in ['MemTotal','MemAvailable','SwapTotal','SwapFree']}
        r['vmstat']={l.split()[0]:int(l.split()[1]) for l in pathlib.Path('/proc/vmstat').read_text().splitlines() if l.split()[0] in ['pswpin','pswpout']}
        r['root_free']=os.statvfs('/').f_bavail*os.statvfs('/').f_frsize
        r['containers']={}
        for name in ['mysql','redis','milvus']:
            full='deephelp-'+name
            p=subprocess.run(['docker','inspect',full],capture_output=True,text=True)
            if p.returncode:continue
            d=json.loads(p.stdout)[0];pid=d['State']['Pid'];c={'state':d['State'],'restart_count':d['RestartCount']}
            if pid:
                cg=pathlib.Path('/sys/fs/cgroup'+pathlib.Path(f'/proc/{pid}/cgroup').read_text().strip().split('::')[1])
                for metric in ['memory.current','memory.peak','memory.swap.current','memory.events','cpu.stat']:
                    fp=cg/metric
                    if fp.exists():c[metric]=fp.read_text().strip()
                c['rss_status']=[l for l in pathlib.Path(f'/proc/{pid}/status').read_text().splitlines() if l.startswith(('VmRSS:','VmHWM:'))]
            r['containers'][name]=c
        r['docker_stats']=subprocess.check_output(['docker','stats','--no-stream','--format','{{json .}}','deephelp-mysql','deephelp-redis','deephelp-milvus'],text=True)
        r['disk']=subprocess.check_output(['du','-s','-B1','/srv/deephelp-data/mysql','/srv/deephelp-data/redis','/srv/deephelp-data/milvus','/var/lib/docker','/var/lib/containerd'],text=True)
        f.write(json.dumps(r)+'\n')
        if time.monotonic()>=end:break
        time.sleep(min(30,max(0,end-time.monotonic())))
print('STABILITY_OBSERVATION_COMPLETE')

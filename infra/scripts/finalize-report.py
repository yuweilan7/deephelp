"""Assemble local acceptance from captured evidence; fails closed on unmet gates."""
import datetime,hashlib,json,pathlib,platform,sys
b=pathlib.Path(__file__).resolve().parents[1];p=b/'reports'
read=lambda name:json.loads((p/name).read_text(encoding='utf-8-sig'))
samples=[json.loads(l) for l in (p/'stability.jsonl').read_text().splitlines()]
duration=samples[-1]['uptime']-samples[0]['uptime']
assert duration>=720,('Incomplete stability observation',duration)
assert 'STABILITY_OBSERVATION_COMPLETE' in (p/'restart-observation.log').read_text()
full=read('client-full.json');post=read('client-post-restart.json');health=read('client-health.json');after=read('infra_after.json')
assert all(x['status']=='PASS' for x in [full,post,health])
assert read('milvus-rebuild.json')['status']=='PASS'
assert all(read('auth-negative-tests.json').values())
assert 'MYSQL_BACKUP_RESTORE PASS' in (p/'mysql-backup-restore.log').read_text()
GiB=2**30;MiB=2**20
available=min(x['meminfo']['MemAvailable'] for x in samples)
free=min(x['root_free'] for x in samples)
assert available>=GiB and free>=12*GiB
resource={}
for name in ['mysql','redis','milvus']:
    states=[x['containers'][name] for x in samples]
    assert all(c['state']['Running'] and c['state']['Health']['Status']=='healthy' and not c['state']['OOMKilled'] and c['restart_count']==0 for c in states)
    assert all('oom_kill 0' in c['memory.events'] for c in states)
    resource[name]={'current_MiB':int(states[-1]['memory.current'])/MiB,'peak_MiB':max(int(c['memory.peak']) for c in states)/MiB,'rss_status':states[-1]['rss_status'],'restarts':0,'oom':False}
    ports=after['containers'][name]['ports']
    for mappings in ports.values():
        if mappings:assert all(m['HostIp']=='127.0.0.1' for m in mappings)
assert samples[-1]['vmstat']==samples[0]['vmstat'],'Swap I/O occurred; review before acceptance'
startup={}
for e in after.get('lifecycle_events',[]):
    name=e.get('Actor',{}).get('Attributes',{}).get('name','')
    if name not in ['deephelp-mysql','deephelp-redis','deephelp-milvus']:continue
    action=e.get('Action','');t=e.get('timeNano',e.get('time',0)*10**9)/10**9
    if action=='start':startup[name]={'start_epoch':t}
    if action=='health_status: healthy' and name in startup and 'healthy_seconds' not in startup[name]:startup[name]['healthy_seconds']=t-startup[name]['start_epoch']
for name,c in samples[0]['containers'].items():
    start=datetime.datetime.fromisoformat(c['state']['StartedAt'].replace('Z','+00:00'))
    logs=[h for h in c['state']['Health']['Log'] if h['ExitCode']==0 and datetime.datetime.fromisoformat(h['End'])>=start]
    if logs:
        first=min(datetime.datetime.fromisoformat(h['End']) for h in logs)
        startup['deephelp-'+name]={'healthy_seconds':(first-start).total_seconds(),'source':'first successful healthcheck after StartedAt in first observation sample'}
assert all('deephelp-'+n in startup for n in ['mysql','redis','milvus'])
result={'P00':'DONE','MILVUS_CLOUD':'ACCEPTED_EXPERIMENTAL','server':'SERVER_VALIDATED','client':'REMOTE_CLIENT_VALIDATED','duration_seconds':duration,'sample_count':len(samples),'minimum_host_available_GiB':available/GiB,'minimum_root_free_GiB':free/GiB,'swap_io_delta':{k:samples[-1]['vmstat'][k]-samples[0]['vmstat'][k] for k in samples[-1]['vmstat']},'resources':resource,'startup':startup,'python_3_12_health_validated':True,'webui':'PAGE_LOADED_DETAILS_EMPTY','wsl':'SCRIPT_GENERATED_SYNTAX_CHECKED_NOT_EXECUTED','host_reboot':'BOOT_POLICY_ENABLED_NOT_REBOOT_TESTED','synthetic_vectors':'interface validation only; embedding quality not evaluated'}
(p/'acceptance-summary.json').write_text(json.dumps(result,indent=2))
lock=read('environment.lock.json');lock['client_runtime']={'python':platform.python_version(),'pymilvus':'2.6.17','platform':platform.platform(),'requirements_sha256':hashlib.sha256((b/'client/requirements.lock.txt').read_bytes()).hexdigest()};(p/'environment.lock.json').write_text(json.dumps(lock,indent=2))
before=read('infra_before.json')
inventory='# 安装前只读盘点\n\n首次记录保留在 infra_before.json；该文件在拉取任何镜像前生成。\n\nEXCLUSION LIST：原有 SSH、DNS、chrony、腾讯云安全/自动化代理、Docker daemon、80/22/ICMP 云防火墙规则；没有既有容器、镜像或项目数据。仅操作 DeepHelp 专属资源。\n'
for cmd,v in before.items():
    if isinstance(v,dict) and 'stdout' in v:inventory+='\n## '+cmd+'\n\n```text\n'+v['stdout']+v['stderr']+'```\n'
(p/'infra_inventory.md').write_text(inventory,encoding='utf-8')
cloud={'source':'Chrome Tencent Lighthouse console, instance detail Firewall tab','instance_id':'lhins-56mgod8p','inbound':[{'protocol':'TCP','port':22,'source':'all IPv4','action':'allow'},{'protocol':'TCP','port':80,'source':'all IPv4','action':'allow'},{'protocol':'ICMP','port':'ALL','source':'all IPv4','action':'allow'}],'no_db_port_rules':True,'mutations':'none'}
(p/'cloud-firewall.json').write_text(json.dumps(cloud,indent=2))
validation=f'''# P00 V2 实际验收

状态：**DONE / REMOTE_CLIENT_VALIDATED**。Milvus 云端接受范围：**低数据、低并发实验**。

## 验收结果

| 项目 | 结果 | 证据 |
|---|---|---|
| MySQL 8.4.11 | RUNNING + HEALTHY；中文/emoji、commit/rollback、唯一键、索引、UTC、20 全局连接/16 业务用户连接上限、权限隔离通过 | client-full.json / server-smoke.log |
| Redis 8.2.8 | RUNNING + HEALTHY；ACL、TTL 到期、64 MiB maxmemory、allkeys-lru、关闭 AOF/RDB 通过 | client-full.json |
| Milvus 2.6.23 | RUNNING + HEALTHY；Chinese Analyzer、BM25 Function、Sparse/Dense、WeightedRanker hybrid、filter、insert/delete 全部通过 | client-full.json |
| 认证负向测试 | MySQL 错密码、Redis 无认证、Milvus 无认证和越权管理请求均拒绝；Milvus root 默认/初始密码已失效 | auth-negative-tests.json / milvus-rbac.json |
| 重启 | MySQL 中文行仍在；Milvus 36 行仍在且被删 id=999 不存在；Redis 缓存丢失符合预期 | client-post-restart.json / observation-queries.jsonl |
| 备份恢复 | MySQL dump→新隔离测试库→查询中文通过；Milvus dataset/schema→新集合 36 行→BM25 通过 | mysql-backup-restore.log / milvus-rebuild.json |
| 本机 | Windows OpenSSH 隧道实际运行，停止/重连/health 已测试；Python 3.12.14 健康和持久化 hybrid 查询通过 | client-health.json / observation-queries.jsonl |
| 公网隔离 | Docker 实际仅发布回环；云防火墙无 DB/UI 入站；本机直连无服务协议响应 | infra_after.json / cloud-firewall.json / public-port-check.json |
| WebUI | Chrome 打开本机隧道 URL 并渲染页面；首页详情表 No Data，未宣称完整遥测显示成功 | 浏览器标签 Milvus Management；webui=PAGE_LOADED_DETAILS_EMPTY |
| Linux/WSL | 脚本已生成且 bash -n 通过；本机无可用 WSL 发行版，未在 WSL 实际执行 | client/tunnel.sh |
| 开机/健康/日志 | systemd 开机启用、先 core 后 Milvus；healthcheck 与有界日志 inspect 通过；本轮未重启整个云主机 | server-smoke.log / environment.lock.json |

## 12 分钟资源观察

连续 {duration:.1f} 秒，{len(samples)} 个样本，约每 30 秒采样。所有样本 healthy；OOM=0，自动重启=0；swap in/out 增量均为 0。宿主 MemAvailable 最低 **{available/GiB:.2f} GiB**，根盘可用最低 **{free/GiB:.2f} GiB**。

| 服务 | 观察末 cgroup memory.current | cgroup memory.peak | 内存上限 |
|---|---|---|---|
| MySQL | {resource['mysql']['current_MiB']:.1f} MiB | {resource['mysql']['peak_MiB']:.1f} MiB | 512 MiB |
| Redis | {resource['redis']['current_MiB']:.1f} MiB | {resource['redis']['peak_MiB']:.1f} MiB | 128 MiB |
| Milvus | {resource['milvus']['current_MiB']:.1f} MiB | {resource['milvus']['peak_MiB']:.1f} MiB | 5120 MiB |

原始 CPU、RSS、memory.current/peak、swap、磁盘、健康、restart/OOM 数据在 stability.jsonl。最后宿主内存、磁盘和数据目录大小在 infra_after.json。Docker 生命周期事件及 start→first healthy 观测在 acceptance-summary.json；该时间包含健康检查采样间隔，并非内部初始化的精确耗时。

首次客户端 Dense/BM25/Hybrid 查询耗时分别约 {full['milvus']['query_ms']['dense']:.1f} / {full['milvus']['query_ms']['bm25_sparse']:.1f} / {full['milvus']['query_ms']['weighted_hybrid']:.1f} ms，包含 Windows 客户端、SSH 与网络开销；不是压测结论。

## 实施中发现并修正的差异

1. 官方 MySQL Release Notes 已列 8.4.12，但官方 Docker 标签不存在。固定可获取的 8.4.11，已与官方仓库 digest 对照。
2. Milvus v2.6.23 tag 内的 standalone_embed.sh 仍写 v2.6.22，且默认全地址发布并额外发布 etcd。保留原脚本与 SHA256，只参照其嵌入式模式；项目 Compose 修正版本、回环发布，不发布 2379，保留 Docker 默认 seccomp。
3. 初次 internal bridge 使 Docker 29 未建立 host 映射：容器内 healthy 但本机连接失败。只重建项目容器/网络，改为普通 bridge + 显式回环发布；后续全部本机验收通过。旧失败记录保留在 pull/startup 日志与本报告，没有伪称首次即成功。
4. 本机 TCP connect 对被阻断端口和未使用的对照端口也可先返回成功，随后 EOF；因此记录应用协议探测与云防火墙/绑定证据，不把单次 connect 返回值冒充公开服务可达。
5. 初始验收使用已有 Python 3.11；随后创建 Python 3.12.14 venv，再次完成认证与持久化混合查询。uv 首次 minor-link 建立报错后改用已经下载的明确解释器路径创建 venv，没有改动系统 Python。

## 边界与下一步

没有业务代码上云，没有购买服务、修改套餐、暴露数据库或改弱 SSH。没有硬配额，DISK_LIMIT_TYPE=SOFT_GUARD。37 条插入/1 条删除以及 36 条重建为合成小数据测试，确定性向量不能验证语义质量。

按照启动包先进入 **M00_ARCHITECTURE**，架构章程完成后进入 **M01_PYTHON_ASYNC_SKELETON**。应用保持本机运行，MySQL 为业务事实来源；LangGraph 暂用本机 SQLite/AsyncSqliteSaver，M15 再评估 checkpoint 持久化；M19 才涉及应用部署。
'''
(p/'validation.md').write_text(validation,encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))

# M27 私有 Langfuse 平台

本目录只部署学习平台，不装配业务观测；业务主链、两个MCP和官方审批图接入属于M28。
Langfuse Web/Worker固定4.50.0，原生Python SDK固定4.16.0（tools依赖/根单锁）。
ClickHouse固定25.12镜像digest，Redis8.2.8独立noeviction/AOF实例，PG18.6独立元数据库，
SeaweedFS4.48独立单机S3/master/volume/filer。镜像全部以compose内digest核对。
SeaweedFS下载镜像与官方Docker Hub manifest digest一致，不依靠镜像站名称证明来源。

## 准备与访问

在现有服务器核对`/srv`及目标目录无软链接后创建`/srv/deephelp-langfuse`，归运行用户所有、0700。
将本目录受控配置复制进去，然后以运行用户执行：

```bash
cd /srv/deephelp-langfuse
bash prepare.sh /srv/deephelp-langfuse
sudo docker compose --env-file runtime.env up -d --wait --wait-timeout 300
```

prepare只首次生成随机凭据，不覆盖旧身份。runtime.env、config/s3.json及config/probe.json均0600，
不提交Git。Web无预置公开密码；实际私人账号由headless初始化创建，账号在runtime.env内。
凭据与SALT/ENCRYPTION_KEY一同保留在受保护备份，不能恢复时重新生成。
所有容器位于专用Compose网络。Web仅服务器loopback13300，S3仅18333；PG/CH/队列及filer/master没有主机公开端口。
不添加公网防火墙规则。Windows按现有SSH/严格host key启动专用转发：

```powershell
.\infra\langfuse\tunnel.ps1 -Action start
.\infra\langfuse\tunnel.ps1 -Action health
```

本机访问`http://127.0.0.1:13300`，SDK使用相同Web端点，S3协议探针使用18333。
内部事件端点为`http://seaweedfs:8333`并强制path-style。首轮只启用文本trace/score；
未配置媒体上传或批量导出，不能把它们的外部presigned端点当成当前已验能力。

## 持久职责与显式证明

PG元数据保存账号/项目/密钥等；Web把原始事件写入S3，专用Redis保存队列引用，Worker从S3消费后写入CH。
`data/postgres`挂载PG18实际根`/var/lib/postgresql`；`data/clickhouse`和logs独立保存。
Redis的`data/redis`包含AOF/元数据，appendfsync everysec有约1秒断电窗口；容器重启不等于断电耐久证明。
SeaweedFS全部内容在`data/seaweedfs`，master显式`/data/master`，filer LevelDB显式`/data/filer`，
volume对象/.idx/.dat及UUID位于同一数据根。不得只备份对象或只保留filer。
health只检查可用性，必须用同一trace验证正文、父子、合成usage及score实际关联。
服务v4的events_only模式使用v2 observations与v3 scores（fields=subject）；旧traces读取接口404不能当未入库。

把服务器config/probe.json经现有安全SSH交付到本机`.local/m27/probe.json`，不要展示其正文。
探针只接受显式live、私有loopback、专用langfuse桶、新报告及有界count/timeout：

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.probes.langfuse_probe --live --config .local/m27/probe.json --mode s3 --create-bucket --output .local/m27/s3-new.json
py -3.14 -m uv run --locked python -m deephelp_tools.probes.langfuse_probe --live --config .local/m27/probe.json --count 1 --output .local/m27/roundtrip-new.json
py -3.14 -m uv run --locked python -m deephelp_tools.probes.langfuse_probe --live --config .local/m27/probe.json --mode verify --source .local/m27/roundtrip-new.json --output .local/m27/verify-new.json
```

send只证明本地SDK结束/flush，不能当远端存入；roundtrip/verify才断言实际API内容。
generation中的17/9 tokens是固定合成字段，实际模型调用及输入/输出/推理token均0。
独立TracerProvider避免改写旧全局Collector装配。原生SDK后台导出，短进程必须flush/shutdown；
异步入口通过to_thread处理flush，不持有业务事务。报告保留脱敏ID/hash/结构，失败保留部分ID以便只复验失败路径。

## 本次运行边界

平台资格试验最多100个小合成trace/轮，每trace两observation及一个score；API子timeout5秒、
S3连接3秒/读取5秒/零自动重试，单轮120秒；较长操作按相应入口显式设置有限超时。
平台进程内存上限和CPU在compose明确；这是单机保护参数，不是企业账号配额或长期容量保证。
全组合试验另启动，至少180秒观察当前应用、两MCP、PG/Milvus/etcd/cache及新旧观测同时运行。
停止边界：MemAvailable低于1GiB持续10秒、swap增长超过256MiB、任一非计划OOM/重启、
空闲磁盘低于8GiB、固定内容API查询超过5秒或积压恢复120秒仍不完整。样本/请求/索引/训练数和实际窗口写最终证据。
失败先恢复本任务停止的组件，不购买资源、不停必要业务服务、不降低模型能力。

官方依据：[v4.50.0](https://github.com/langfuse/langfuse/releases/tag/v4.50.0)、
[SDK](https://langfuse.com/docs/observability/sdk/advanced-features)、
[SeaweedFS接入](https://langfuse.com/self-hosting/deployment/infrastructure/blobstorage#seaweedfs)、
[队列](https://langfuse.com/self-hosting/deployment/infrastructure/cache)。实际交付证据见M27 handoff。

## 故障、容量与恢复入口

异步取消资格使用同一显式探针的`--mode cancel`：取消等待任务、闭合原生观测、在线程中flush，
断言事件循环仍推进并从实际API读回endTime/output。关闭导出使用`--mode disabled`，断网或S3不可用
使用send并独立核对未入库；不能把SDK静默结束当远端成功，也不能假定失败事件恢复后会自动重发。
组件停止/重建前结束本轮写者，保留已有数据；恢复后verify复用原ID。业务接入仍留M28。

服务器在全部必要组件运行时显式启动采样（sudo只为读Docker/cgroup；报告0600）：

```bash
sudo /opt/deephelp-app/current/.venv/bin/python capacity.py --live --seconds 180 \
  --trace-id EXISTING_SYNTHETIC_TRACE --output /srv/deephelp-langfuse/config/capacity-new.json
```

采样读取15个实际容器和app/business/retrieval服务、主机内存/swap/CPU/磁盘，以及固定内容API。
工作负载由当前任务显式启动，不自动调用模型、训练或改现行发布。计划内Worker停止先创建
`config/capacity-worker-stopped`，只允许这个组件暂停；结束后移除。采样不能单独证明负载已执行，
必须配套保存索引/查询/训练、队列积压/恢复及业务等待的实际内容证据。cgroup同时用量与单组峰值
分别记录，独立训练进程另计RSS，主机MemAvailable覆盖全机。不重置累计业务预算。

`backup.sh LABEL`只冷停本平台六组件，归档PG/CH/Redis AOF/SeaweedFS全数据根、身份/加密参数和
对应compose/config；生成受保护tar.gz与sha256，EXIT trap恢复平台，失败保留.partial/必要锁。
备份期间停止所有平台SDK/人工写者。旧业务服务保持；不能把平台停机解释为业务数据库停写。

```bash
cd /srv/deephelp-langfuse
bash backup.sh NEW_LABEL
bash restore.sh /srv/deephelp-langfuse/backups/NEW_LABEL.tar.gz \
  /srv/deephelp-langfuse-restore-NEWNAME
```

恢复目标必须全新且符合`/srv/deephelp-langfuse-restore-[a-z0-9-]+`；校验归档hash后用Python3.14
tar data filter拒绝越界路径/链接，保留CH根内相对metadata链接并恢复PG/CH目录用户。
沿用原身份/盐/加密密钥，仅改Compose project和私有Web13310/S3 18343端口；不重新生成密钥。
在本机容量下，先停止源平台，再运行隔离副本；这项恢复不证明两套平台同时容量。
实际verify历史trace/score、待消费队列引用和同一S3对象hash/metadata后，停止副本、恢复源平台。
源平台和归档保留；只清理本次新建且已核对路径的隔离副本，不删Docker全局镜像/卷或旧环境。

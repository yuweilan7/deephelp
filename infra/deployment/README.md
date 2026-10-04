# M30 PostgreSQL/Langfuse 个人云运行

正式入口为一个工作台应用、独立Business/Retrieval MCP和可选合成权益进程；关系事实与官方checkpoint在应用PG，原生trace进入Langfuse。Prometheus/Grafana保留，三个应用进程各自暴露loopback指标。M30已完成正式单写与旧服务退役；旧M23源码/脚本由`bak/mysql-otel`保留，旧安装/源数据/冷备继续作为恢复资料。当前交付状态以PROJECT_STATE和M30 handoff为准。

## 安装与控制

根Python3.14.7、uv0.12.13、单一uv.lock和三个包保持。使用根锁导出`requirements.lock`，三包sdist→wheel，连同`start.sh`生成SHA256SUMS。`install.sh ABSOLUTE_BUNDLE_DIR VERSION`只安装新的不可覆盖版本，核对完整hash与Python/包/FastText/SDK；安装成功不等于完整内容发布通过。

受保护持久根为`/srv/deephelp-modern`，配置见runtime.env.example。复制已有凭据、来源/受引用最小资产闭包和历史状态，秘密独立交付，0600/0700。显式`DEEPHELP_POSTGRES_CONFIG`和`DEEPHELP_LANGFUSE_CONFIG`是唯一新运行装配；不配置旧OTLP/Tempo端点。保留原签名字节，用artifact-map只映射读文件位置，不重写旧manifest、审批或向量空间。

`deephelp-modern@.service`读取该根的runtime.env，执行`/opt/deephelp-app/modern/start.sh`。将control/install/switch/backup/restore脚本及`infra/postgres/backup.sh`、`restore.sh`分别交付至`/opt/deephelp-app/bin/`，后两者命名postgres-backup.sh、postgres-restore.sh。控制入口：

```bash
sudo bash /opt/deephelp-app/bin/control.sh start
sudo bash /opt/deephelp-app/bin/control.sh health
sudo bash /opt/deephelp-app/bin/control.sh stop
sudo bash /opt/deephelp-app/bin/switch.sh INSTALLED_VERSION
```

stop同时停止应用、两个MCP和权益进程，systemd结束其子进程组；检查MainPID和状态。人工维护/外部CLI/审核发布写者也必须结束，并核验实际数据库连接及在途状态。health证明服务与直接指标可用，内容验收另做。switch只切安装文件及重启，不更新内容active，不重放业务；失败保留现场，不能自动回退陈旧数据库。

首项隔离演练使用新PG库、8100/8111/8112/8115和19464–19466，配置`DEEPHELP_ISOLATED_REHEARSAL=1`才允许不带完整release channel。正式切换须删除该标记，使用精确内容验收通过的PG完整发布channel；8000/8011/8012/8015及9464–9466不得与旧入口并行占用。访问通过原personal SSH隧道，本机浏览器18080，Grafana23000；没有公网TLS或SLA声明。

## 直接指标

官方PrometheusMetricReader按抓取从原SDK度量动作读取，独立registry/loopback端口/关闭生命周期；保留W3C与Langfuse SDK内部OTel。拒绝同时启用直接指标和OTLP度量转发。原指标operations/outcomes/inflight/model tokens/unknown usage/retries及duration秒直方图保持；标签只有固定动作/状态/model/方向/service，不带用户、订单、run或query。

同一model.attempt的真实input/output只计一次，未知usage另计，已包含在output的推理不相加。重复抓取不产生新执行，receipt重放不产生新model attempt。成功比例只以completed/failed为分母，零分母无值；取消/拒绝独立显示。小样本分位数保留至少100个动作的门槛。看板不推断Langfuse内部队列长度或零丢span。

首项新指标栈模板metrics.compose.yaml/metrics.prometheus.yaml使用host网络但HTTP只绑定loopback，抓取三个应用出口及Milvus原生指标。Prometheus19092、Grafana13002便于与旧观测并行演练；替换后客户端需映射实际Grafana端口。资源报告同时记录主机/cgroup/磁盘，Langfuse不能代替这部分。

依据：[官方Python Prometheus出口](https://opentelemetry.io/docs/languages/python/exporters/#prometheus)、[锁定0.66b0](https://pypi.org/project/opentelemetry-exporter-prometheus/0.66b0/)。不启动多worker共享registry；每个服务一个受控进程。

## 一致备份与隔离恢复

```bash
sudo bash /opt/deephelp-app/bin/backup.sh NEW_LABEL
sudo bash /opt/deephelp-app/bin/restore.sh /srv/deephelp-backup/modern-NEW_LABEL \
  deephelp_m25_m30_restore_NEWNAME /srv/deephelp-modern-restore-NEWNAME
```

备份先停所有modern单元并拒绝未登记PG连接；再custom-format dump应用全schema（含官方checkpoint/发布/关系/观测登记）。M29独立学习数据库另存learning.dump，不合并不同演示的审批/效果状态。随后冷停Langfuse六组件、保存元PG/CH/Redis AOF/SeaweedFS对象/master/filer及身份密钥，并冷备新Prometheus/Grafana目录。保存运行根、受引用工件、任务/M29状态与当前/保留A/B安装材料（依赖hash清单、三wheel、启动文件）；EXIT恢复已停服务，失败保留必要现场/锁。进行备份前须结束SDK/平台人工写者；业务与平台之间没有跨库事务。

恢复只允许新命名库与新绝对运行根，校验SHA256SUMS、pg_restore单事务和Python tar data filter；只调整部署配置/映射target，原签名资产不改。按备用端口启动恢复副本，核对所有表行hash、历史/关系/官方图与同run平台内容。Langfuse归档通过infra/langfuse/restore.sh另恢复到新根，在容量允许前先停止源平台，验后恢复源；不双挂数据目录。learning.dump同样恢复到独立新库以验证M29记录，禁止把它作为第二活动业务写入口。

工具入口`python -m deephelp_tools.probes.modern_deployment_probe --live --config ABS_CONFIG --compare-config ABS_SOURCE_CONFIG --output NEW_REPORT`对所有PG应用/归档/官方表作只读一致性原值行hash比较；可追加明确auth/base-url与metrics-ports，实际查询Business/历史、鉴权/schema和直接指标，模型0次。平台仍需实际内容/usage/Score、Dataset/Prompt关联读回，health和文件hash不足以替代。

## 切换与回退边界

维护窗口先停止旧应用、Business/Retrieval、权益、任务与人工写者，记录在途/UNKNOWN，源最后只读一致快照导入新PG并转换M26官方状态。核对全表/关系、审批原计划/版本/效果及签名闭包。只启动新路线一个活动写入口；缓存/向量按原signature/归属/version校验复用或重建。

运行字节变化必须新的完整内容门禁及manifest，保留旧PASS原样；不改旧hash换通过。新库未写前可恢复旧入口与快照；新库已写后只回滚兼容当前PG schema且其对应manifest已验的应用安装，不能直接回切陈旧MySQL。跨路线回退缺反向迁移/对账证据时停止。

只有新发布、云浏览器业务/历史/审批/同trace、幂等/越权、故障重启、隔离恢复与容量通过后才退役旧MySQL/Collector/Tempo容器及自启；数据卷、旧安装、源快照、Tempo/Grafana必要资料及bak分支保留。退役与删除恢复资料分别处理，不默认删卷或采购资源。

当前已验兼容PG安装为A `m30-deployment-v1`、B `m30-retirement-v2`；两版代码不同且各有完整内容发布，实际B→A→B已通过。当前B active/A previous、channel `m30-personal` revision4。回滚须先停止全部写者，用目标安装的CLI核对实际revision并激活它对应的已验manifest，再切安装；下面REV是status现场读取的值，不能沿用历史revision：

```bash
sudo -i
bash /opt/deephelp-app/bin/control.sh stop
cd /srv/deephelp-modern
set -a; source config/runtime.env; set +a
/opt/deephelp-app/releases/m30-deployment-v1/.venv/bin/python \
  -m deephelp_app.bootstrap.release_cli status --live --channel m30-personal
/opt/deephelp-app/releases/m30-deployment-v1/.venv/bin/python \
  -m deephelp_app.bootstrap.release_cli activate --live --channel m30-personal \
  --release eec3f952d3c188b0381dfd8066fb420033f52ca20eb11bc9c2677a5ec6ef8c52 \
  --expected-revision REV --budget-state /srv/deephelp-modern/state/budget.json
bash /opt/deephelp-app/bin/switch.sh m30-deployment-v1
```

恢复B时同样停写、由B安装CLI读取新revision，activate B hash
`2f07f93212fed925c4f975cc43343313dca549e75eac97863c00b5f80ddfc5b5`，再switch B。
任何守卫/启动失败保留停写现场，核对同库的状态和manifest；不重新启用旧MySQL。

最终一致备份是`/srv/deephelp-backup/modern-cutover-final`，含独立learning.dump、
metrics.tar.gz、retained-installations.tar.gz及整体SHA256SUMS；对应Langfuse冷归档在
`/srv/deephelp-langfuse/backups`。旧配置/秘密/容器镜像元数据及MySQL/观测冷备在
`/srv/deephelp-modern/recovery/legacy-controls`，原数据目录及安装继续保留。旧恢复材料
只用于隔离读取/恢复或另有完整反向迁移方案的操作，不能直接恢复成第二活动写入口。
新副本恢复按上面新库/新根协议，先停源平台以满足单机容量，再实际比较内容、指标旧
时间点及审批/历史；校验通过后删除本次独立副本，受保护归档保持。

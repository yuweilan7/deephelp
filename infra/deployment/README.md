# M30 PostgreSQL/Langfuse 个人云运行

正式入口为一个工作台应用、独立Business/Retrieval MCP和可选合成权益进程；关系事实与官方checkpoint在应用PG，原生trace进入Langfuse。Prometheus/Grafana保留，三个应用进程各自暴露loopback指标。M30已完成正式单写与旧服务退役；旧M23源码/脚本由`bak/mysql-otel`保留，旧安装/源数据/冷备继续作为恢复资料。当前交付状态以PROJECT_STATE和M30 handoff为准。

## M31 策略发布与恢复

start.sh通过`DEEPHELP_PROVIDERS`将同一模型配置传给应用及Retrieval服务，缺省仍用原公开模板；启用native rerank时另外明确完整`DEEPHELP_RERANK_URL`。同时绑定发布指针、相邻reference语料和PG release channel，不能只给运行中的旧profile增加模型。providers/env不含客户端可选端点。

正式默认使用关闭策略，新开启策略保存在独立已验收channel；当前安装、channel/hash/revision见PROJECT_STATE。两版都通过11会话14消息内容门禁，开启版另有独立MCP/PG主链和四路学习内容证据。选择开启版时先control.sh stop，核对PG该channel指向已验证完整清单，再一并改为开启版指针、语料/providers和release channel，重建四单元；不能只改应用channel而保留不同的Retrieval profile。保留原业务/审批/UNKNOWN/预算状态，切换不重放消息。

切回旧M30安装须同时恢复旧完整release与其指针/运行配置。受保护恢复闭包`/srv/deephelp-modern/recovery/m31-rerank-v1`记录原安装、active/previous和配置字节；先停止四单元，读取最新PG revision，以CAS切换到记录的原active，恢复闭包中的runtime.env与pointer.json，再执行switch.sh对应旧安装。artifact-map未改，旧A/B文件和安装仍保留；不回退数据库快照、不清零累计预算。仅切symlink而不恢复精确release/profile会被拒绝。

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

工具入口`python -m deephelp_tools.probes.platform.modern_deployment_probe --live --config ABS_CONFIG --compare-config ABS_SOURCE_CONFIG --output NEW_REPORT`对所有PG应用/归档/官方表作只读一致性原值行hash比较；可追加明确auth/base-url与metrics-ports，实际查询Business/历史、鉴权/schema和直接指标，模型0次。平台仍需实际内容/usage/Score、Dataset/Prompt关联读回，health和文件hash不足以替代。

## 切换与回退边界

维护窗口先停止旧应用、Business/Retrieval、权益、任务与人工写者，记录在途/UNKNOWN，源最后只读一致快照导入新PG并转换M26官方状态。核对全表/关系、审批原计划/版本/效果及签名闭包。只启动新路线一个活动写入口；缓存/向量按原signature/归属/version校验复用或重建。

运行字节变化必须新的完整内容门禁及manifest，保留旧PASS原样；不改旧hash换通过。新库未写前可恢复旧入口与快照；新库已写后只回滚兼容当前PG schema且其对应manifest已验的应用安装，不能直接回切陈旧MySQL。跨路线回退缺反向迁移/对账证据时停止。

只有新发布、云浏览器业务/历史/审批/同trace、幂等/越权、故障重启、隔离恢复与容量通过后才退役旧MySQL/Collector/Tempo容器及自启；数据卷、旧安装、源快照、Tempo/Grafana必要资料及bak分支保留。退役与删除恢复资料分别处理，不默认删卷或采购资源。

M30当时已验兼容PG安装为A `m30-deployment-v1`、B `m30-retirement-v2`；两版代码不同且各有完整内容发布，实际B→A→B已通过，当时B active/A previous、channel `m30-personal` revision4。今日安装/发布以PROJECT_STATE为准；这段保留旧版本恢复关系。回滚须先停止全部写者，用目标安装的CLI核对实际revision并激活它对应的已验manifest，再切安装；下面REV是status现场读取的值，不能沿用历史revision：

```bash
sudo -i
bash /opt/deephelp-app/bin/control.sh stop
cd /srv/deephelp-modern
set -a; source config/runtime.env; set +a
/opt/deephelp-app/releases/m30-deployment-v1/.venv/bin/python \
  -m deephelp_app.bootstrap.commands.release_cli status --live --channel m30-personal
/opt/deephelp-app/releases/m30-deployment-v1/.venv/bin/python \
  -m deephelp_app.bootstrap.commands.release_cli activate --live --channel m30-personal \
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

## M34 固定个人入口配置

`start.sh app`要求`DEEPHELP_PERSONAL_IDENTITY`指向受保护JSON，仅含现有`tenant_id/user_id`（[模板](../../modules/deephelp-app/personal-identity.example.json)），必须唯一匹配`state/auth.json`的一条身份。不增加新token/owner；原历史、学习catalog owner、审批绑定、MCP委托及预算不变。其他服务继续使用原独立凭据，个人入口不替代服务鉴权。

本机/SSH同源工作台直接进入；重启/过期由`GET /auth/session`协商新的HttpOnly会话与CSRF，写操作仍需有效标记，原Bearer API继续可用。当前个人identity来自既有learning catalog owner，不能按auth行顺序猜选。`install.sh`在sudo安装时将新版本目录交给systemd原`ubuntu`用户，并且不跟随Python链接；0700私有安装不能保持root独占导致服务203/EXEC。

M34正式安装/真实浏览器业务、维护CAS/外身份守卫、应用重启及m34→m31→m34兼容回退已通过，[交接](../../handoffs/M34.md)记录证据边界。当前安装/完整release/hash/revision只写PROJECT_STATE。入口仍是原personal隧道；页面/历史/受保护观测只读不调用模型，显式执行业务问题才调用。已有synthetic审批继续归原身份，个人页面不会接管别人的审批。

恢复闭包`/srv/deephelp-modern/recovery/m34-personal-v1`保留原runtime.env、pointer.json、artifact-map.json、原安装/release及新的new-runtime.env。回退旧版先停止四单元和其他写者，恢复该闭包的原runtime.env/pointer.json（0600，ubuntu可读），导出runtime.env，使用**目标旧安装**的CLI读取当前PG revision，再激活闭包记录的原完整release，最后切旧安装：

```bash
sudo bash /opt/deephelp-app/bin/control.sh stop
cd /srv/deephelp-modern
sudo install -o ubuntu -g ubuntu -m 600 recovery/m34-personal-v1/runtime.env config/runtime.env
sudo install -o ubuntu -g ubuntu -m 600 recovery/m34-personal-v1/pointer.json state/pointer.json
set -a
source config/runtime.env
set +a
/opt/deephelp-app/releases/m31-rerank-v1/.venv/bin/python \
  -m deephelp_app.bootstrap.commands.release_cli status --live --channel m30-personal
/opt/deephelp-app/releases/m31-rerank-v1/.venv/bin/python \
  -m deephelp_app.bootstrap.commands.release_cli activate --live --channel m30-personal \
  --release RETAINED_OLD_HASH --expected-revision OBSERVED_REVISION \
  --budget-state /srv/deephelp-modern/state/budget.json
sudo bash /opt/deephelp-app/bin/switch.sh m31-rerank-v1
```

`RETAINED_OLD_HASH`取受保护recovery.json记录的原active，`OBSERVED_REVISION`取刚刚status的结果。恢复新免登录版同样停写，使用new-runtime.env和原pointer.json，换成m34-personal-v1的CLI，重新读取revision并激活其完整hash后switch。两次切换都核对当前PG新订单/历史/原receipt/原审批，不恢复旧数据库、不覆写累计预算、不重放副作用；旧安装恢复原登录界面属于该保留版本的既有行为。配置/安装/完整发布须成套匹配，不能只切symlink。

## M35 Vue同源交付与恢复

先按[前端入口](../../modules/deephelp-app/frontend/README.md)在本机安装锁定依赖、测试并build，再构建app的sdist/wheel；wheel只带built。三包/依赖锁/start.sh构成原安装bundle，云端仅启动Python同源服务。新静态字节改变runtime-v2，内容报告、完整清单及独立安装必须对应同一构建，不能将旧内容PASS改hash后挪给新包。当前已验版本与完整hash见PROJECT_STATE。

发布CLI使用原ubuntu应用用户，保留管理员加载的既有runtime.env环境；管理员root写入0600预算文件会使应用无法读取。停写后用sudo安装配置，随后用sudo -u ubuntu -E执行CLI，不改预算内容/上限或建立新账号。M35恢复闭包保留在受保护recovery/m35-vue-v2（原M34配置）及recovery/m35-vue-v3（最终新配置/安装bundle/前后域快照）。历史run仍引用的旧安装、清单与工件继续保留。

恢复M34使用m35-vue-v2的runtime.env、learning-catalog.json、pointer.json；恢复最终M35使用m35-vue-v3的new-runtime.env、new-learning-catalog.json、pointer.json。均先control.sh stop，逐文件sudo install -o ubuntu -g ubuntu -m 600到原config/state对应位置，再重新source runtime.env。M34安装的旧CLI入口是deephelp_app.bootstrap.release_cli；M35入口是deephelp_app.bootstrap.commands.release_cli。不要把源码移动后的模块路径套到未更新的保留安装。

读取对应CLI status的实际revision后，使用该安装的完整hash执行CAS activate；新版本示例如下，参数取当前受保护清单/recovery记录：

```bash
cd /srv/deephelp-modern
set -a
source config/runtime.env
set +a
sudo -u ubuntu -E /opt/deephelp-app/releases/m35-vue-v3/.venv/bin/python \
  -m deephelp_app.bootstrap.commands.release_cli status --live --channel m30-personal
sudo -u ubuntu -E /opt/deephelp-app/releases/m35-vue-v3/.venv/bin/python \
  -m deephelp_app.bootstrap.commands.release_cli activate --live --channel m30-personal \
  --release RETAINED_NEW_HASH --expected-revision OBSERVED_REVISION \
  --budget-state /srv/deephelp-modern/state/budget.json
sudo bash /opt/deephelp-app/bin/switch.sh m35-vue-v3
sudo bash /opt/deephelp-app/bin/control.sh health
```

重启/旧新恢复只读取已有会话和原receipt核对，不重跑大模型。比较新订单版本、原receipt整体hash、审批/审计/唯一效果/调用及累计预算；平台诊断不可用不能改变业务结果或触发重放。详细通过范围与失败修复见M35交接。

## 仅切换通用Chat模型

运行源码/静态字节未变时复用匹配安装，但新provider、assets描述、精确内容报告及完整清单必须重新绑定，不能改旧清单。发布CLI的--manifest必须指向DEEPHELP_STATE_ROOT内受控文件；将已验证原字节清单用sudo install -o ubuntu -g ubuntu -m 600复制到state，再以sudo -u ubuntu -E登记。模型配置传给应用和Retrieval，catalog同时指向新候选配置/报告，原历史映射继续保留。

切换或恢复前先control.sh stop，让运行进程正常释放预算锁，再保存或安装对应runtime.env/catalog，source新环境并按实际revision执行CAS activate。control.sh start返回不等于端口已就绪；在本次设置的有界等待内检查/health实际status=ok，超时保存服务诊断并恢复匹配配置及完整发布。不能把正常运行的锁当作过期锁删除，也不覆盖数据库或累计预算。

本次模型切换恢复闭包recovery/qwen38-flash-default-v1保存原M35 Max的runtime.env/learning-catalog.json/pointer.json，以及新new-runtime.env/new-learning-catalog.json/recovery.json和前后域核对。两者都复用m35-vue-v3及同一事实库；具体已验结果见M03交接，当前发布见PROJECT_STATE。

## M36运行解释交付与恢复

M36沿用三包、同源built和原个人身份/Flash配置。源码/静态字节变化后须新精确内容报告、完整清单和独立安装；最终状态标签修正的报告/安装独立于初版保留。实际八场景、包边界、重启与旧新切换见[M36交接](../../handoffs/M36.md)，当前安装/hash/revision只写PROJECT_STATE。

最终恢复闭包`recovery/m36-explainable-v2`保存兼容原M35 Flash的runtime.env、learning-catalog.json、pointer.json与新new-runtime.env/new-learning-catalog.json；recovery.json标明对应完整hash/安装。原M36初版受历史运行引用，连同其闭包保留。回退先control.sh stop结束所有写者，逐文件用sudo install -o ubuntu -g ubuntu -m 600恢复原config/runtime.env、config/learning-catalog.json、state/pointer.json；恢复新版改用new-runtime.env/new-learning-catalog.json与原pointer.json。

两次切换均重新source runtime.env，以原ubuntu用户执行**目标安装**的deephelp_app.bootstrap.commands.release_cli status，读现场revision；再activate该闭包对应的完整hash及--expected-revision，使用原state/budget.json，最后switch对应安装并有界等实际health。M35目标为m35-vue-v3，M36目标取PROJECT_STATE；不能只切symlink或沿用旧revision。登记新manifest须先原字节复制到state目录以满足路径守卫。

重启与切换使用同一现行PG，只读已有会话/原receipt、审批原计划/审计/唯一效果/下游调用、官方checkpoint并核对表内容hash。最终核对摘要在受保护final-recovery.json；不覆盖累计预算、恢复旧数据库或为补观测重发业务。Langfuse缺失应保留PG事实和unknown，固定动作/取消产生的无终态记录继续按原归属保护。清理仅针对本次已关闭进程和已提取且无消费者的传输副本，安装、清单、配置、凭据与恢复资料保持。


## M40 专业Agent协作安装与恢复

M40沿原三包/同源built、PG、个人身份、原providers与两HTTP MCP部署；角色策略JSON纳入原完整发布字节，默认single，用户明确选择collaboration才执行。当前安装/精确hash/revision见PROJECT_STATE，实际内容、任务/同run观测与恢复限制见[M40交接](../../handoffs/M40.md#特性3原业务对照精确安装与恢复)。

恢复闭包`/srv/deephelp-modern/recovery/m40-collaboration-v1`含原/新runtime.env、learning-catalog.json/new-learning-catalog.json、原artifact-map.json/pointer.json、recovery.json及验证过的bundle。当前路径映射、检索签名与providers未变；manifest保护最终内容报告和原采集器FAIL，不裁剪/覆盖这些文件。云budget是原累计writer，不能用本机副本替换。

回退先control.sh stop结束四单元和其他写者，恢复原runtime.env/learning-catalog.json/pointer.json；恢复M40改用new-runtime.env/new-learning-catalog.json和原pointer。逐文件sudo install -o ubuntu -g ubuntu -m 600到原config/state位置，重新source环境。用**目标安装**的deephelp_app.bootstrap.commands.release_cli status现场读取revision，再activate恢复闭包记录的完整hash及--expected-revision、原state/budget.json，最后switch.sh对应安装并有界health。旧目标m36-explainable-v2，新目标m40-collaboration-v1；两个精确hash取recovery.json，不沿用历史revision。

M36严格类型不识别M40新字段；旧版只读其原有会话，不继续M40消息/协作。新增任务/回执保留PG，返回M40后读取同一run并核对facts/evidence/call/版本。恢复不回滚数据库或重放业务，UNKNOWN/审批不接管；只读Milvus发布守卫仍计原attempts，模型用量/费用/未知预留不增加。本次33张表仅发布active/events预期变化、旧新receipt与同run观测已验，不证明跨不兼容schema的回退或任意RUNNING重领。

安装器通过sudo运行时显式提供已验证uv的PATH与UV_PYTHON_INSTALL_DIR；本环境uv在原ubuntu的.local/bin，Python3.14.7在其uv目录。首次必须确认目标不存在，不覆盖已有版本；失败先核对实际阶段再续，不把安装成功当内容门禁。

## M41 Skill安装与恢复

M41沿原三包、Python3.14.7及根uv.lock安装；Skill审核快照、原件hash和本次内容PASS纳入原完整ReleaseManifest。当前安装/完整hash/revision见[PROJECT_STATE](../../docs/PROJECT_STATE.md)，正式内容、管理页面、同run观测及恢复证据见[M41交接](../../handoffs/M41.md#特性3管理审核快照精确安装与恢复)。构建仍用根workspace；导出独立安装依赖须包含三个成员，不能只导出无依赖的虚拟根：

```powershell
$bundlePath = "<本轮已创建bundle的绝对路径>"
py -3.14 -m uv export --frozen --all-packages --no-dev --no-editable --no-emit-workspace --format requirements-txt --output-file (Join-Path $bundlePath 'requirements.lock')
```

包wheel由同次sdist构建并单独安装；依赖、三包和start.sh按bundle的SHA256SUMS核对。失败安装未激活时先核对现场阶段和原active，修正依赖或文件所有者后只续未成功步骤，不覆写有效安装或重新套旧PASS。

恢复闭包`/srv/deephelp-modern/recovery/m41-skills-v1`保留原/新runtime.env、原/新learning-catalog、原artifact-map/pointer、recovery.json及已验证bundle。旧目标`m40-collaboration-v1`，新目标`m41-skills-v1`；精确hash取该recovery.json。回退先control.sh stop并核对四单元MainPID=0及其他写者；逐文件以ubuntu所有者/0600恢复原runtime.env、learning-catalog.json、pointer.json，重新source环境。恢复新版本用new-runtime.env/new-learning-catalog.json及原pointer；不要改写累计budget。

两方向都由**目标安装**的`deephelp_app.bootstrap.commands.release_cli status --live --channel m30-personal`读取当前PG revision，再用目标完整hash、现场`--expected-revision`与原`--budget-state /srv/deephelp-modern/state/budget.json`执行activate；最后switch.sh目标安装、有界检查实际health。现成参数结构见上方恢复命令；不能只切symlink、沿用旧revision或用本机预算替代云累计状态。Skill候选/批准/拒绝记录仍在原catalog的task_root/skills，恢复不回滚它们。

实际M41→M40→M41中完整33张非系统表（业务、官方与旧checkpoint）只见发布active/审计变化，模型计量保持；发布资产读取仍计RPC attempts。M40严格DTO只读原M40历史，新Skill字段/回执保留在PG，回M41读原记录；不让旧版继续新会话，不重放UNKNOWN或业务副作用。

## M42 六叶子安装与恢复

当前安装/hash/revision见[PROJECT_STATE](../../docs/PROJECT_STATE.md)，内容与恢复范围见[M42交接](../../handoffs/M42.md#f3六叶子对照完整发布与恢复)。恢复闭包`/srv/deephelp-modern/recovery/m42-business-v1`保留原/新runtime.env、learning-catalog、原pointer/artifact-map、recovery.json与精确bundle。旧目标m41-skills-v1，新目标m42-business-v1；先停止四单元并核对MainPID=0及无其他写者，以ubuntu/0600恢复目标配置，重新source，再使用**目标安装**的release_cli status读现场PG revision，activate对应完整hash及现场expected-revision，最后switch目标安装并有界检查真实health。

M41目标使用原state/budget.json，M42目标使用state-m42/budget.json；M42预算由本次本机累计值移交，切换不清零，不把old-budget.json等快照复制到活动账本。目标配置同时决定catalog、providers、pointer、state_root及task_root；不能只切symlink。新task_root在state-m42/tasks，保留原16份已结束任务/审核字节；原任务根不改。业务MCP启动通过DEEPHELP_BUSINESS_CATALOG选择complaints-v3，旧配置默认complaints-v1。登记清单须满足原state目录路径守卫。

旧新切换不回滚PG、不重放业务或审批/UNKNOWN。新诊断密钥下读取旧run采用停写后的PG主体/session验证与原日志HMAC核对，只重映射scope hash；原事件内容、时间、事实/工具版本及原密钥/日志保持。对应diagnostic-scope-import.json与new-restored-final.json保留来源核验，跨主体仍404，读取与重放零模型调用。manifest绑定的辅助.gitignore也要原字节安装并通过SHA256SUMS；不能为方便修改完整清单或覆盖旧共享providers。

## M43 审核SOP安装与恢复

当前完整hash、revision、运行/工具摘要见[PROJECT_STATE](../../docs/PROJECT_STATE.md)，本次实际内容与37张PG表恢复比较见[M43交接](../../handoffs/M43.md#f3-不可变审核完整发布与精确恢复)。新安装`m43-sop-author-v1`，旧目标`m42-business-v1`；恢复闭包`/srv/deephelp-modern/recovery/m43-sop-author-v1`保存旧runtime.env/learning-catalog.json/artifact-map.json/pointer.json、新new-runtime.env/new-learning-catalog.json/new-artifact-map.json、原字节bundle、recovery.json。被绑定的完整清单及审核/内容原件在`/srv/deephelp-modern/artifacts-m43/.local/m43`；旧state-m42/legacy与更早闭包继续保留。

新配置使用`artifacts-m43/.local/m43`作为state和task_root，单一artifact-map把原Windows仓库根映射到`/srv/deephelp-modern/artifacts-m43`；不能叠加与旧根重叠的多个映射。首次旧共享judge配置hash不匹配时已拒绝覆盖，完整M43依赖放入独立根；不得用旧文件替代或改写原清单。

恢复仍用既有控制脚本：先`sudo bash /opt/deephelp-app/bin/control.sh stop`，核对四单元MainPID=0及目标预算无writer；以ubuntu/0600从闭包恢复**目标**runtime/catalog/artifact-map并重新source。使用**目标安装**的`.venv/bin/python -m deephelp_app.bootstrap.commands.release_cli status --live --channel m30-personal`读取现场revision，再activate目标完整hash与现场expected-revision，最后`sudo bash /opt/deephelp-app/bin/switch.sh 目标安装ID`并有界核对health。M42使用原state-m42/budget.json，M43使用artifacts-m43/.local/m43/budget.json；不把old-budget-at-cutover.json、任何报告快照或本机已移交的预算复制到活动账本。恢复时原模型tokens/费用保持，元数据核验仍计RPC attempts；budget.lock由原writer关闭，不手工清除有效锁。

M43实际采用37→38→39→40 CAS链并回到M43；后续操作必须读现场revision。旧问题/审批/UNKNOWN的原SOP及参数未迁移或重演，旧安装只读自身兼容历史。新完整版本的登记/激活会先复核审核原件、相同候选内容及安装字节；保存/预演/审核/prepare都不能替代完整发布。Windows→云路径解析属于精确闭包适配，原报告与清单路径/hash保持不可变。

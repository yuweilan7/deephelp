# 本地启动与依赖交接

这是运行环境入口。现有三类业务使用 `python -m deephelp_app serve`，完整版本使用release_cli serve；参数见[业务启动入口](../modules/deephelp-app/README.md#业务启动入口)。中间件可连接、模型目录可读不代表内容验收。进度见 [PROJECT_STATE](PROJECT_STATE.md)。

用户2026-10-03更新交付约定：所有验收仅在特性分支完成，main只合并、保存和同步代码。当前文档仅列特性分支验收命令；历史main证据留Git历史与handoff，当前规则以AGENTS/ROADMAP为准。

M06使用本机自建正式SDK stdio服务及M02合成数据，无模型额度或云库配置。预览、真实协议验收与参数见[应用README](../modules/deephelp-app/README.md#m06真实mcp只读工具)。其--live只启动受控本机进程，不运行P00或开启应用converse业务路径。

M07默认演示使用固定模型回放加本机真实stdio，不消费模型额度；显式--live才调用千问。输入是合成已分类Question和专用业务fixture，入口见[应用README](../modules/deephelp-app/README.md#m07最小sop执行)。

## 验收范围与证据复用

普通回归默认离线；真实模型验收按变更影响选择；完整真实评测按明确目标单独启动。先按[决策表](ROADMAP.md#按影响选择验收)说明行为、样本/反例、预期调用与token范围、含修复余量的共享上限及无需重验项；再选下方一个权威入口。同会话所需能力的模型/endpoint/协议/相关配置和目标未变时复用已通过内容证据，不重复chat/schema/tool/embed套餐。运行报告路径或阶段变化不要求重新预检。

下方各模块的完整命令保留为明确启动相应能力/质量评测时的入口，不能串成普通特性门禁；历史PLAN/handoff含main复验的命令已被AGENTS取代，历史记录不改。模型HTTP200、实库连通、固定动作恢复和离线PASS分别证明其范围，不互相冒充。pytest与CI只做离线检查；根conftest在收集前阻止远程DNS/TCP，保留本机HTTP/stdio。pytest --live仍只检查配置，不解除网络隔离。

临时输出与收尾遵循[AGENTS](../AGENTS.md#临时产物与任务收尾)：系统临时目录或`.local/tmp/<任务标识>/`承接过程产物，最终证据/资产与控制文件分开。M17完整报告已含逐行内容；成功并保存最终报告后自动移除等价rows.jsonl，失败/中断保留行日志供定位。既有不可变清单和累计预算保持；必要证据提取后删除本轮脚本、渲染、临时安装与构建目录。

## 本机配置与新机器接手

同机先读 `.local/DEPENDENCIES.md`：凭据路径、实例、端口、最近检查及恢复入口；机器索引为 `.local/dependency-access.json`。两者被 Git 忽略，不含秘密正文。

| 依赖 | 加载入口 | 新机器要提供 |
|---|---|---|
| 千问 | 根 `.env.local`，由 `uv run --env-file .env.local` 显式加载 | Key、对应 Base URL；字段见 [应用模板](../.env.example) |
| SSH / 中间件地址 | `infra/config/connections.env` | 实际主机、账号、端口和私钥路径；见 [连接模板](../infra/config/connections.env.example) |
| MySQL / Redis / Milvus 密码 | `DEEPHELP_SECRET_FILE` 指定文件，默认 `~/.deephelp/.env.secret` | 与既有服务一致的应用及管理密码；见 [密码模板](../infra/config/secrets.env.example) |
| SSH 主机身份 | 本机 `infra/client/known_hosts` | 经独立核对的目标 host key；保持严格校验 |
| 腾讯云管理 API | 当前运行不使用 | 新增云管理任务时另配 SecretId / SecretKey |

复制模板为真实配置，凭据通过已有安全渠道交付。可将密码和私钥放在项目 `.secrets/`，修改私钥路径，并在运行探针的同一 PowerShell 会话设置 `$env:DEEPHELP_SECRET_FILE = Join-Path (Get-Location) '.secrets/middleware.env'`。既有凭据无需搬动。`.env.local`、`.secrets/`、`.local/`、私钥、连接配置和 known_hosts 都被忽略；克隆公共仓库不会取得这些文件。`infra/.env` 仅配置镜像版本。

## Windows：在仓库根目录执行

Python 为 3.14.7，应用依赖由根 uv workspace / 单一 `uv.lock` 管理。根 `.python-version` 固定 3.14.7，根 pyproject 要求 uv `>=0.12.13,<0.13`。Windows 的 Python 启动器入口统一为 `py -3.14`；先确认解释器和 uv：

```powershell
py -3.14 --version
py -3.14 -m uv --version
# 仅 uv 缺失或低于项目要求时安装/升级；本机已验证 0.12.13。
py -3.14 -m pip install --upgrade "uv==0.12.13"
```

首次准备依赖并启动业务（复用已配置身份、累计预算和已发布指针）：

```powershell
py -3.14 -m uv sync --locked --all-packages
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app serve --pointer .local/m09/active.json --auth .local/m08/auth.json --budget-state .local/m08/session-budget.json
```

配置只从环境读取，不自动发现 dotenv。业务serve显式组装真实端口；已有身份/预算不重置，新机器先按M08入口init/migrate。仅学习骨架使用 `uvicorn deephelp_app.app:create_app --factory --host 127.0.0.1 --port 8000`，其dev/test模式使用fake、`/converse`保持501。业务启动不导入probes/evaluation/learning，也不读取包内dev/test；显式故障配置及评测命令才加载对应工具，见[资源职责](../modules/deephelp-app/src/deephelp_app/assets/README.md)。

解释器核验：`py -3.14 -m uv run --locked python -c "import sys; assert sys.version_info[:3] == (3, 14, 7); print(sys.executable)"`。旧启动入口的历史验证原文保留 Git 历史，当前运行命令统一使用 3.14.7。uv 为独立工具；版本门禁不会降低应用的 Python 要求。

中间件客户端使用单独的既有 P00 环境，不新增根锁。首次安装后复用即可：

```powershell
py -3.14 -m uv venv --python 3.14.7 infra/.venv314
py -3.14 -m uv pip install --python infra/.venv314/Scripts/python.exe --requirement infra/client/requirements.lock.txt
.\infra\client\tunnel.ps1 -Action start
.\infra\client\tunnel.ps1 -Action health
```

`health` 做 MySQL SELECT、Redis PING、Milvus 版本/数据库/集合列表读取，报告为 `.local/infra-health/client-health.json`。`check` 只测端口；用 `-Action stop` 关闭本脚本管理的隧道。POSIX 入口、备份和恢复见 [infra](../infra/README.md) 与 [运维说明](../infra/OPERATIONS.md)。

当前是单人学习环境，三个中间件应用账号采用完整权限，不保留P00的命令/键前缀/角色及账号连接配额限制。权限更新和内容验收见[运维入口](../infra/OPERATIONS.md#学习环境权限)。这与应用代码中的用户归属、幂等和业务事实校验分别维护。

## 模型记录按需读取

先读 `.local/model-pool-summary.md`，再查询 `.local/model-pool.json` 中所需候选；原始目录/额度/用量快照由该索引引用。核对快照时间、到期时间和当前账户额度；已验证范围见 PROJECT_STATE，不将两个候选的实测外推到全部目录。

候选保留额度状态及观察时间；模型使用遵循 [AGENTS](../AGENTS.md)。免费额度用完可换用能力合适的候选，也可使用账号余额继续调用；当前代码尚未实现自动切换，须显式更新模型配置并完成对应真实验收。Embedding签名变化必须新版本集合与重建，不能将同维模型静默混入旧集合。目录和额度快照不能替代实际可调用性与能力验证。

## M03 受控真实验收

配置模板为 [providers.example.json](../modules/deephelp-app/providers.example.json)，能力及兼容规则见 [模型矩阵](MODEL_CAPABILITIES.md)。凭据只通过环境变量读取，chat/embed可分别配置；调用前核验鉴权、可调用性和所需能力。脚本不修改账户开关，也不自动选择替代模型。

运行上限由当前任务设置，覆盖必要输入、输出、诊断与复验。脚本仍要求调用/token/费用参数及.local累计文件，用于防止失控调用；恢复执行沿用原累计记录，不归零。字段和历史报告留.local，具体金额与用量不复制到常驻说明。先查看参数，再设置本次验证的`$probeBudgetFile`、`$probeReportFile`、`$probeCalls`、`$probeTokens`和`$probeCost`：

`$probeBudgetFile`须指向已初始化的.local累计文件：任务上限字段为`max_calls`、`max_tokens`、`max_cost_cny`，计数为`attempts`、`tokens`、`charged_tokens`，并保留`cost_upper_cny`、`uncertain_attempts`及`stages`。新任务从零初始化计数；恢复已有任务不得清零。

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.probes.live_probe --help
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.probes.live_probe --live --capability schema --stage feature --budget-state $probeBudgetFile --output $probeReportFile --max-calls $probeCalls --max-tokens $probeTokens --max-cost $probeCost
```

明确完整能力验收使用`--all-capabilities`替代`--capability schema`；只选所需能力可重复`--capability chat|schema|tool|embed|extended_chat|thinking_schema`。完整能力命令追加 `--extended` 验证41条消息/76KB合成输入、8192 token输出参数及开启推理的严格schema内容。特性扩展验收共6次请求；保留原累计预算并使用新报告。输出8192是请求参数验收，不是生成8192 token的质量测试。ProviderConfig可设置默认推理及思考额度，ChatRequest可按任务覆盖；当前既有业务路径默认仍为非思考模式。

特性分支仅检查受影响能力；全部新接入能力仍分别验收，main不复验。共享预算在实际发请求前落盘，单进程文件锁阻止并发穿透；超时/用量未知保留预留占用。live 重试为 0，默认 pytest 仍离线；pytest 的 `--live` 只是配置检查，真实验收使用上述独立入口。模型响应结构诊断只记录 usage、维度、索引、工具名和安全请求 ID，不记录 prompt、正文、参数、凭据或隐藏推理。

## M04固定内容验收

默认演示离线，入口见[应用README](../modules/deephelp-app/README.md)。真实验收使用版本化合成黄金样本及固定补充边界；用`--list-cases`离线列出ID，`--case`可重复选择改变行为、反例及相邻样本；明确全量实体验收才用`--all-cases`跑22例，包括长文/否定/更正/未知、两类API字段和直接/引号混合订单冲突。配置可选strong-providers支持层间升级；仅显式`--strong-content`另验直接强端口内容；这项与实际层间升级分别记录。报告包含来源、未解析字段、分层命中/调用/耗时及脱敏trace；命中率口径为该层接受至少一个观察的样本数/经过该层的样本数，不是模型准确率。

先查看帮助，设置本任务的累计文件、报告及调用/token/费用上限。累计文件字段与M03相同，恢复继续沿用，不能归零；--output必须与预算/锁文件不同。可选强端口是独立ProviderConfig文件，只改chat候选及保守单价配置，放.local，核对能力并实测后注入；不用或不可恢复时按AGENTS收口。当前M04已验证qwen3.8-flash/qwen3.8-max，不能外推其他模型。

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.probes.text_entity_probe --help
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.probes.text_entity_probe --live --case quoted-order --stage feature --budget-state $entityBudgetFile --output $entityReportFile --max-calls $entityCalls --max-tokens $entityTokens --max-cost $entityCost
# 需要强端口时追加：--strong-providers $entityStrongProviders
# 恢复当前特性的验收时使用新报告，继续原累计预算；main不复验。
```

live使用transport retries=0、子timeout和共享总deadline；预算预留先落盘、独占锁阻止并发穿透。金额为保守配置估算，不是账单。必要最终报告、有效失败诊断及累计预算仅留.local，过程产物收尾删除，不提交客户数据或凭据；此入口不访问云业务库/MCP/P00 full，也不执行SOP。

## M05导入与Dense检索

从根运行，默认preview离线；其他命令必须显式`--live`，复用已有Milvus应用账号、SSH只读容量探针和M03 provider配置。先health/所需能力证据/容量，再写M05专用合成集合。每次运行设置足够的调用/token/费用累计上限，沿用M03字段：max_calls/max_tokens/max_cost_cny、attempts/tokens/charged_tokens/cost_upper_cny/uncertain_attempts；文件在.local，恢复不得归零。M05调用计数包括Milvus应用层操作，token/费用只计模型调用；默认重试0、总timeout300秒，可显式调整。PyMilvus2.6.17在SDK timeout非空时按时间窗口重试而非retry_times；本适配器使用SDK timeout=None/retry_times=0，外层async deadline把每个操作限制在15秒。

设置本任务的`$denseBudgetFile`、`$denseManifestFile`、`$densePointerFile`和`$denseReportFile`后执行：

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.dense_cli preview
py -3.14 -m uv run --locked python -m deephelp_app.dense_cli --help
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.dense_cli accept --live --budget-state $denseBudgetFile --manifest $denseManifestFile --pointer $densePointerFile --output $denseReportFile
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.dense_cli verify --live --budget-state $denseBudgetFile --manifest $denseManifestFile --output $denseReportFile
```

默认namespace=m05_synthetic、dataset-version=m05-smoke-v1。自备文件先preview，运行时加`--source`；CSV/JSONL/XLSX字典见[合成数据说明](../modules/deephelp-app/src/deephelp_app/assets/README.md)。`import`导入/续跑，`search --query`检索，`evaluate`独立dev评测；`accept`额外检查重复导入和内容指标，新manifest时模拟一次真实upsert后的客户端确认丢失（不宣称云故障）。

新版本使用新的dataset-version/manifest，`activate`回读验证后切本机指针，`rollback`复验上版再回退；指针只保留一层上一版本。`verify`要求已有完整manifest，不导入/修复数据，用新进程检查完整记录、逐条FP32向量哈希和真实查询；它不自行重启服务。真正重启按[运维](../infra/OPERATIONS.md)与AGENTS授权执行后再verify，记录前后容器StartedAt，不能以重连代替重启。维护删除为`delete --allow-delete-synthetic --doc-id`，只删除指定scope内已有合成记录；恢复用同语料的新manifest，不覆盖旧验证证据。

输出/预算/manifest/指针应使用不同.local路径；必要最终内容证据、有效诊断与累计预算只留.local。公共仓库只含机制、合成数据及必要交接。该入口不访问业务MySQL/Redis、不运行P00 full/MCP/SOP；实际限制见PROJECT_STATE。

## M07真实模型与SOP验收

先按模型摘要核对任务所需chat候选的原生工具能力与可调用性，M07不调用Embedding或云业务库。真实验收用现有ProviderConfig、共享预算和自建MCP只读服务；身份/订单/券均合成。配置与Prompt版本固定，未做主意图识别或回复润色。

本轮设置足够的调用/token/费用/总时限，并初始化专用`.local`累计文件；字段沿用M03的`max_calls/max_tokens/max_cost_cny`、`attempts/tokens/charged_tokens/cost_upper_cny/uncertain_attempts/stages`。调用数包含模型与MCP实际尝试，费用是保守配置估算。恢复沿用原文件、不清零；独占锁、发请求前持久预留与未知尝试不退款防止穿透。报告须为新的`.local`文件，与预算/锁路径不同。

设置本次的`$sopBudgetFile`、`$sopReportFile`、`$sopCalls`、`$sopTokens`、`$sopCost`后，从根执行：

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.probes.sop_probe --help
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.probes.sop_probe --live --stage feature --budget-state $sopBudgetFile --output $sopReportFile --max-calls $sopCalls --max-tokens $sopTokens --max-cost $sopCost
# 恢复时使用新报告路径，累计预算保持同一文件。
```

明确启动完整SOP内容验收时该入口验三类正常查询、过期券、空活动、用户/工具注入及三个下游失败；小改动优先使用M17选择受影响会话，不叠加本入口。完整范围核对实际事实、证据与ledger并核对资源退出。探针重试0、transport retries=0、默认总300秒，支持--timeout；执行器本身的有限重试由离线测试验证。PASS与退出0都满足才验收。必要最终结果、有效模型/MCP诊断及累计用量留.local；重复阶段结果按收尾规则删除；无P00 full、企业数据、数据库迁移或业务converse。


## M08闭环启动与验收

先按上文health检查既有隧道/云库，核对选定chat/Embedding可调用性；Dense默认`.local/m05/active.json`，签名变化不得直接复用。初始化/迁移/服务/CLI提问见[应用README](../modules/deephelp-app/README.md#m08单条完整问题闭环)。复用现有MySQL/Milvus应用账号、千问配置及本机正式SDK合成MCP，不运行P00 full，不重建服务。

同机已有`.local/m08/auth.json`和`session-budget.json`可复用；独立演示或新机器用mvp_cli init创建新的文件，设本次足够上限。auth须包含样本请求者固定身份。累计格式沿M03计数/上界，恢复不清零；只有serve和显式probe --live联网。

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.mvp_cli --help
py -3.14 -m uv run --locked python -m deephelp_app.probes.mvp_probe --output .local/m08/offline-new.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.probes.mvp_probe --live --http --stage feature --output .local/m08/feature-new.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.probes.mvp_probe --live --http --samples --stage feature --output .local/m08/samples-new.json
# 恢复当前特性的验收时沿用累计预算，换新输出文件。
```

--auth/--budget-state/--pointer/--providers可指定；输出须新文件且不与控制文件及派生锁/trace冲突。默认离线跑完整36条，保留旧预期差异；以下两条live是不同可选范围，不要求连续执行。明确完整M08验收时验三类、Dense、缺槽位、未知、下游500、注入、过期券、空活动，另选12条原始固定样本。检查实际HTTP、事实/证据/ledger、MySQL终态、重投/冲突、8并发唯一接收、身份隔离、新资源回放和退出。报告PASS和退出0同时满足才验收。

## M09中文BM25与融合对照

仅索引/查询受影响时检查health与所需Embedding内容/分词/服务端稀疏能力；已适用的能力证据可复用，再用现有专用Milvus。默认preview离线；命令、冻结语料与三路对照见[应用README](../modules/deephelp-app/README.md#m09中文bm25与融合对照)。复用既有provider和M05容量/manifest机制，但创建独立M09版本集合，保留旧Dense集合。默认K=3；导入和查询使用同一版本的服务端Jieba配置，分词可直接analyze核对。

本轮先设置足够的`$hybridCalls`、`$hybridTokens`、`$hybridCost`及专用`$hybridBudgetFile`；`hybrid_cli init --budget-state $hybridBudgetFile --max-calls $hybridCalls --max-tokens $hybridTokens --max-cost $hybridCost`只创建本机累计文件，已有文件拒绝重置。其他live命令继续传同一--budget-state；不同命令使用新的--output，不能覆盖证据或控制文件。SDK timeout=None/retry_times=0、15秒外层子时限，整体默认1800秒；必要最终报告、有效诊断与本次累计预算只留.local。

`tune`只在dev选择0.25/0.5/0.75的Dense权重，按candidate Recall@1→MRR→接近0.5→较小权重选择，冻结index/dev/test摘要及K。`evaluate --classify`只用已冻结策略运行test：按三方案实际返回证据调用同一600分类逻辑，多诉求/无关含确定性守卫；不执行SOP。不得根据test错例改权重后仍宣称未见test。`activate`验证完整内容/向量哈希后发布语料与策略；`rollback`验证并原子恢复上一组合，服务须重建应用资源才读取新指针。

M08运行命令追加`--pointer .local/m09/active.json`即可用同一600 Hybrid端口；默认仍为`.local/m05/active.json`。M09只读verify/compare及M08真实HTTP路径在特性分支验收，继续同一累计预算；不跑P00 full，不查询历史/事件集合。该小合成对照不证明企业效果、15+情境或500+规模。

## M10 多问题记忆

M10继续使用现有MySQL/Redis/Milvus与选定Embedding签名。升级执行增量`mvp_cli migrate`，serve/ask接入显式问题续接，Redis窗口会在读取时恢复。Milvus事件投影用独立`memory_cli project`运行，`memory_cli show --query`检索后回查MySQL；完整命令见[应用README](../modules/deephelp-app/README.md#m10多问题记忆)。不要把原意图集合用作用户历史，也不要重置累计预算。

## M14 SOP治理

离线校验/发布/回退、固定合成情境及真实验收参数见[应用运行入口](../modules/deephelp-app/README.md#m14-sop治理与场景验收)。复用现有模型、M08/M10专用业务表、M09指针及本机正式SDK stdio，默认应用使用包内治理注册表；自建发布目录用`mvp_cli serve --sop-directory`，在新装配时生效。先核对模型候选与health，新任务设置独立累计预算，恢复沿用同一文件/新报告。只验真实服务与合成业务；审批计划是人工移交数据，M15之前不执行变更，不运行P00 full。

## M18 反馈演示

完整操作见[应用入口](../modules/deephelp-app/README.md#m18-审核后的反馈闭环)。使用现有专用MySQL的四张增量反馈表、Milvus新版本集合、M13 CPU训练接口及正式SDK合成只读工具；不新增依赖或改根锁。通用Chat按用户授权改为qwen3.8-max，切换先验chat/schema/tool；Embedding仍为原qwen3.7-text-embedding-flash签名。初始化独立累计预算和稳定脱敏key，恢复沿用原文件，报告和资产用新目录。构建检查保留双版本的实际容量，所有模型/云资源工件留.local。默认M09指针不换；本机演示active是独立控制文件；完整发布使用release_cli/MySQL单一active，参见应用入口。

## M15 持久审批

运行/明确批准/恢复与两个进程saver门禁见[应用入口](../modules/deephelp-app/README.md#m15-持久审批与恢复)。先health，再用增量015迁移创建现有MySQL上的独立审批、操作、checkpoint及合成效果表。LangGraph及其saver协议版本由根uv.lock锁定，使用应用自建MySQL适配，无新增数据库或云服务。loopback合成下游与应用需保留同一32-byte服务key；真实模型/HTTP验收继续原模型、M09指针和独立任务累计预算。恢复不重置预算，报告用新.local路径。进程恢复只覆盖已登记审批工作流，企业支付/退款/补偿或通用RUNNING自动领取未启用。

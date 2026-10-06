# 当前学习与运行入口

本地负责运行核心Python/Agent原理，云端保留完整线上教学。先选目的，再准备对应配置；不要求本机复制云端的组件、页面和数据。

| 目的 | 入口 | 准备与证据边界 |
|---|---|---|
| 本地核心原理 | `python -m deephelp_tools.learning.course <topic>` | 新克隆、Python3.14.7、根uv.lock；不读旧.local/云配置，无Node或中间件要求；固定响应/内存状态证明机制 |
| 已部署完整云教学 | personal隧道后浏览18080 | 本机浏览器/SSH；真实PG/模型/两个MCP与观测在云端；当前安装见STATE，历史读取0模型 |
| 自行装配基础真实业务 | `python -m deephelp_app serve` | 明确选择业务MCP、PG/模型/身份/预算/检索与发布资产；提供业务/历史/解释，未装配的学习路由不能使用 |
| 自行装配完整学习工作台 | `python -m deephelp_tools.learning.workbench serve` | 复用基础serve全部参数，另按需配置学习catalog/合成权益；缺配置保持实际空态，页面存在不代表功能已装配 |
| 历史安装/数据恢复 | deployment与对应handoff | 原安装、清单、配置与当前PG须兼容；旧MySQL/Tempo资料仅供恢复，不作为默认入口 |

本地最短命令：

```powershell
py -3.14 -m uv sync --locked --all-packages
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course request
```

准备依赖可能下载包；课程本身不联网，准备后可将`uv run --locked`改为`uv run --offline --locked`。无需`.env.local`、身份、累计预算、发布指针或SSH隧道。具体输入/状态/源码、其他topic和原审批反例见[课程最短路径](learning/README.md#本地核心最短路径)；12项topic（含协作）不是每次运行清单。源码editable安装不要求前端built，需要本地网页或正式wheel/sdist时才按[前端入口](../modules/deephelp-app/frontend/README.md)先构建。

完整云访问见[当前云端个人入口](#当前云端个人入口)。PG/Langfuse专用隧道仅供明确的本机实库/平台操作，普通课程及云工作台访问无需启动；基础infra转发仅Redis/Milvus。M31默认关闭重排，开启版须完整channel/profile一起选择，见[M31运行入口](../modules/deephelp-app/README.md#m31-专用重排端口)。安装/访问/恢复见[deployment](../infra/deployment/README.md)，状态只见PROJECT_STATE。

# 本地启动与依赖交接

自行启动真实业务才需显式设置 `DEEPHELP_POSTGRES_CONFIG` 指向应用库五字段JSON，入口见[PG迁移与恢复](../infra/postgres/README.md)。执行app的 `migrate` 同时建立业务表和独立schema内的官方checkpoint迁移；serve只检查并打开已建立的保存器。旧P00 MySQL配置是退役源恢复资料，正式health只检查Redis/Milvus；不能用旧配置启动新源码。M26审批执行/恢复已接官方保存器，旧状态须显式转换；新完整发布需要新运行字节的适用证据。

原生观测显式设置`DEEPHELP_LANGFUSE_CONFIG`指向服务器私有JSON，字段见[模板](../modules/deephelp-app/native-observation.example.json)。host/public_key/secret_key由已部署项目提供，首次配置生成32随机字节hex的redaction_key并持久保存；恢复必须使用同一密钥，模板零值须替换。enabled=false用于停用导出。配置同样传给两个MCP进程；原业务权限、模型请求/预算保持。旧OTLP参数会被拒绝，未登记到原生平台的旧run明确返回retired；旧部署/Tempo历史材料见M30交接，不启动旧后端查询。

这是运行环境入口。显式demo使用 `python -m deephelp_app serve --mcp-module deephelp_tools.demo.mcp_server --corpus demo/data/m09_corpus.jsonl`；新源码PostgreSQL可维护记录先启动[独立Business服务](../modules/deephelp-services/README.md#启动与凭据)，应用选择`--business-mcp-url`及持久服务凭据。独立检索见[Retrieval启动](../modules/deephelp-services/README.md#独立检索服务)，应用另选`--retrieval-mcp-url`与其专用凭据；省略该组时仍用原本地检索。完整发布使用精确清单及明确release channel；最新实际发布见PROJECT_STATE。中间件可连接、模型目录可读不代表内容验收。

## 验收范围与证据复用

普通回归默认离线；真实模型验收按变更影响选择；完整真实评测按明确目标单独启动。先按[决策表](ROADMAP.md#按影响选择验收)说明行为、样本/反例、预期调用与token范围、含修复余量的共享上限及无需重验项；再选下方一个权威入口。同会话所需能力的模型/endpoint/协议/相关配置和目标未变时复用已通过内容证据，不重复chat/schema/tool/embed套餐。运行报告路径或阶段变化不要求重新预检。

各包README保留相应能力的命令，按选定范围执行；它们不是每个特性的必跑清单。验收与Git交付遵循[AGENTS](../AGENTS.md#分支测试合并)，历史handoff保留当时的结果。pytest与CI只做离线检查；根conftest在收集前阻止远程DNS/TCP，保留本机HTTP/stdio。pytest --live仍只检查配置，不解除网络隔离。MCP的本机合成协议、固定响应演示和真实模型内容各有独立证据边界。

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

首次准备锁定依赖并运行核心课程：

```powershell
py -3.14 -m uv sync --locked --all-packages
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course request
```

配置只从环境读取，不自动发现dotenv；课程不读取业务配置。自行装配真实服务时先看`py -3.14 -m uv run --locked python -m deephelp_app serve --help`及[业务入口](../modules/deephelp-app/README.md#业务启动入口)，身份/预算仅新环境使用init，已有文件不重置；业务/官方saver迁移按PG入口。选择PG业务资料和独立检索时启动[两个MCP服务](../modules/deephelp-services/README.md)，原身份与委托凭据必须匹配；显式stdio demo仍需tools与包内合成资料。Hybrid选取对应发布语料/指针，正式运行另绑定完整release channel，不能直接借用旧.local/m08、m09路径或旧manifest。完整学习路由使用workbench入口，catalog及权益配置见[学习扩展](../modules/deephelp-tools/README.md#浏览器学习扩展)。

依赖故障关闭，没有自动JSON/回放回退。仅M01生命周期骨架使用`uvicorn deephelp_app.api.app:create_app --factory --host 127.0.0.1 --port 8000`，`/converse`保持501，不作为客诉/完整学习启动入口。业务启动不导入冻结dev/test，安装与离线命令见[工具包](../modules/deephelp-tools/README.md)。

解释器核验：`py -3.14 -m uv run --locked python -c "import sys; assert sys.version_info[:3] == (3, 14, 7); print(sys.executable)"`。旧启动入口的历史验证原文保留 Git 历史，当前运行命令统一使用 3.14.7。uv 为独立工具；版本门禁不会降低应用的 Python 要求。

中间件客户端使用单独的既有 P00 环境，不新增根锁。首次安装后复用即可：

```powershell
py -3.14 -m uv venv --python 3.14.7 infra/.venv314
py -3.14 -m uv pip install --python infra/.venv314/Scripts/python.exe --requirement infra/client/requirements.lock.txt
.\infra\client\tunnel.ps1 -Action start
.\infra\client\tunnel.ps1 -Action health
```

基础`health`做Redis鉴权PING、Milvus版本/数据库/集合列表读取，报告为`.local/infra-health/client-health.json`；PG与Langfuse用各自专用入口。`check`只测端口；用`-Action stop`关闭对应profile管理的隧道。POSIX入口、备份和恢复见[infra](../infra/README.md)与[运维说明](../infra/OPERATIONS.md)。

当前是单人学习环境，三个中间件应用账号采用完整权限，不保留P00的命令/键前缀/角色及账号连接配额限制。权限更新和内容验收见[运维入口](../infra/OPERATIONS.md#学习环境权限)。这与应用代码中的用户归属、幂等和业务事实校验分别维护。

## 模型记录按需读取

先读 `.local/model-pool-summary.md`，再查询 `.local/model-pool.json` 中所需候选；原始目录/额度/用量快照由该索引引用。核对快照时间、到期时间和当前账户额度；已验证范围见 PROJECT_STATE，不将两个候选的实测外推到全部目录。

候选保留额度状态及观察时间；模型使用遵循[AGENTS](../AGENTS.md)。通用Chat长期固定qwen3.8-flash，免费额度用完可用余额，不自动换其他通用模型；归属/强判、Embedding与Rerank按各自明确配置。当前实际选择见STATE，摘要中的历史额度不代表今日余额。Embedding签名变化必须新版本集合与重建，不能将同维模型静默混入旧集合；目录/额度快照不代替实际能力验证。

## 能力与内容验收入口

本页只放接手/装配入口，详细参数和可运行命令由包README及对应CLI的--help维护。按[影响表](ROADMAP.md#按影响选择验收)选一个权威路径，样本/预算由本次任务确定；历史完整命令不串成普通门禁。

| 目标 | 入口 |
|---|---|
| 模型chat/schema/tool/embed、实体抽取 | [应用按改动验收](../modules/deephelp-app/README.md#按改动选择验收)，models.live_probe/text_entity_probe的--help |
| Dense/BM25/Hybrid、训练/资产、冻结评测 | [工具包](../modules/deephelp-tools/README.md)，对应evaluation的preview/audit/--help |
| Business/Retrieval MCP协议和固定事实 | [独立服务](../modules/deephelp-services/README.md)，双端schema/归属和实库内容分别证明 |
| 审批/恢复/完整发布/反馈重任务 | [应用](../modules/deephelp-app/README.md)及[工具包](../modules/deephelp-tools/README.md)，保持原身份/累计预算/精确资产与恢复闭包 |

### M07真实模型与SOP验收

原入口deephelp_tools.probes.conversation.sop_probe的固定响应演示和显式--live分开；合成事实/工具及共享预算参数见[应用M07](../modules/deephelp-app/README.md#m07最小sop执行)。小改动选受影响会话，不额外叠加完整探针。

### M08闭环启动与验收

原单消息入口deephelp_tools.probes.conversation.mvp_probe，初始化/启动/提问见[应用M08](../modules/deephelp-app/README.md#m08单条完整问题闭环)。指定当前PG/语料/发布资产，旧Dense路径只是历史默认。

### M09中文BM25与融合对照

共享向量/候选/语料的三路比较见[应用M09](../modules/deephelp-app/README.md)。Embedding签名/集合保持，协议与模型内容各证明其范围。

## 当前云端个人入口

部署、版本切换、受保护配置与恢复见[服务器运行入口](../infra/deployment/README.md)，当前事实见PROJECT_STATE，[M37交接](../handoffs/M37.md)记录该项只读观察，原八场景见[M36](../handoffs/M36.md)，协作证据见[M40](../handoffs/M40.md)，Skill与历史剩余收尾见[M41](../handoffs/M41.md)，当前语料/精确安装/恢复见[M44](../handoffs/M44.md)。M23为旧MySQL/Tempo路线历史。本机只运行浏览器和现有SSH隧道，应用/两个HTTP MCP/中间件在服务器运行，无需本机前端或业务进程。

```powershell
./infra/client/tunnel.ps1 -Profile personal -Action start
./infra/client/tunnel.ps1 -Profile personal -Action health
```

工作台 `http://127.0.0.1:18080/`；Grafana `http://127.0.0.1:23000/`。personal 与默认 infra profile 分别管理进程/端口/状态文件。私有入口使用既有严格 known_hosts 的 SSH 加密链路，没有公网 TLS。M34工作台直接进入显式配置的原个人身份，无需寻找令牌；刷新/服务重启后自动重建会话，PG历史仍从原账本读取。服务端`DEEPHELP_PERSONAL_IDENTITY`唯一匹配原auth文件，浏览器不持有其中Bearer令牌。云端累计预算为独立持久 writer，不能用过期的本机预算副本覆盖它。新安装及兼容回退入口见[部署说明](../infra/deployment/README.md#m34-固定个人入口配置)，当前安装/发布见PROJECT_STATE。

先在“对话与全过程”刷新持久历史、选择已有消息，读取事实/时间线/不可变Business证据，再去“观测诊断”读取同次run；“识别与检索”“多轮与记忆”“工具与工作流”沿原记录学习，“评测与改进”只读取受控报告与版本即可。当前数据为可维护的合成学习资料，PG/MCP/模型协议为真实执行；浏览不是重新执行。明确提问、真实检索比较、任务构建/验证或审批/恢复才执行对应动作，页面会说明预算和边界。

完整教学不表示所有可选配置都已具备：个人身份不接管synthetic审批练习；当前catalog未配置Langfuse历史映射，页面保持未配置/按钮禁用，原run的原生Langfuse诊断仍可读。原13段主链不全由LangGraph运行，图定义也不证明某操作已经走过。未登记旧观测为retired、缺span/usage/CPU/排队按实未知；不为补观测重发业务或在云依赖失败时切成本地回放。新机器缺SSH配置按上方模板准备，隧道health只证明入口可达，事实/解释以原记录核对。

## Git同步排障

pull出现Cannot fast-forward to multiple branches时，先核对跟踪配置和实际远端。可使用不写共享FETCH_HEAD的fetch后显式快进origin/main，避免多个可合并条目的干扰；具体命令为`git fetch --no-write-fetch-head origin`和`git merge --ff-only origin/main`。基线变化和冲突按AGENTS自主修复，不手工伪造Git状态、不强推，验收仍在特性分支。

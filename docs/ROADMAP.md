# DeepHelp 路线图

只维护能力顺序与验收入口。当前进度见 [PROJECT_STATE](PROJECT_STATE.md)，Git交付见 [AGENTS](../AGENTS.md)，运行方法见 [LOCAL_SETUP](LOCAL_SETUP.md)。

## 复刻范围

复刻PDF核心机制，原件不是完整源码/语料/企业接口。按p73–74先闭环再深化：

1. M08最小闭环：三类客诉，真实千问、Milvus和MCP协议，业务数据合成。
2. 核心深化：Hybrid、记忆、事件聚合、完整级联、FastText、SOP治理、调试、评测与审核回流。
3. 个人学习与远端运行：M19工程分层→M20持久业务→M21可观测性→M22学习工作台→M23个人云部署。M15已提供审批基础；真实业务写工具仍须实际执行适配与专项验收。
4. 用户2026-10-04追加现代技术路线：旧技术栈已保存在bak/mysql-otel，main继续演进；M25全业务PostgreSQL→M26官方checkpoint→M27 Langfuse平台→M28原生观测→M30新环境切换/旧服务退役；M29 Prompt/评测平台为推荐学习扩展。另明确追加M31 qwen3.7-text-rerank接入/效果验证，可独立于迁移启动。模块尚未实施，见下方独立路线。

身份隔离、幂等、预算和事实证据贯穿核心。15+情境、500+样本为完整规模目标，未完成单列限制，不用小样本冒充。

## 来源到能力

| PDF文件页 | 机制 | 模块与工程替代 |
|---|---|---|
| 15–18 | 分层、模型适配 | M01/M03/M08；Python替代Spring |
| 19–24 | 清洗、实体提取 | M04；Regex+受限API，保留否定/尾订单/来源 |
| 25–35 | 规则→召回→记忆→兜底 | M02/M05/M09/M12/M13，阈值由dev校准 |
| 35–41 | Dense/BM25/融合 | M05/M09；API/FP32不是BGE-M3同空间 |
| 41–54 | FastText、反馈 | M13/M17/M18；独立样本，候选审核 |
| 55–61 | 聚合、13段 | M10–M12；只有600进入主意图服务 |
| 62–66 | 记忆、生命周期 | M08/M10；Redis/MySQL/Milvus职责分开 |
| 67–69 | SOP/MCP/ReAct/输出 | M06–M08/M14/M16；真实协议+合成下游，写操作需M15 |
| 70–76 | 调试、评测、分期 | M08/M16–M18；实际覆盖与规模分开 |

内部后台/京ME、全供应商、本地大模型及多Agent不默认逐项建设；另需验证时明确目标。M19–M23及M25–M31为用户2026-10-04指定的个人学习/工程扩展，Rerank已另立M31，不宣称来自PDF原实现。细页码见 [SOURCE_MAP](SOURCE_MAP.md)，裁决见 [DECISIONS](DECISIONS.md)。

## 能力与退出条件

规格在MODULES，仅在实施对应模块时读。

| M | 成果与验收 |
|---|---|
| M00 | 已完成设计；架构、契约、来源和走查可解释 |
| M01 | 已完成骨架；生命周期、取消、fake/trace和离线测试 |
| M02 | 唯一类型、3–5类意图、30–60条固定样本；不提前实现审批/库 |
| M03 | chat/schema/tool/embed分别实测，预算/错误可诊断 |
| M04 | 尾订单、否定、前导零、更正保真，未知不猜 |
| M05 | 专用Milvus语料导入/检索；幂等、signature和split隔离 |
| M06 | 真实list_tools/call_tool；两个只读合成工具，鉴权/取消可验 |
| M07 | 三SOP驱动正常/缺资料/失败，工具循环有界 |
| M08 | 首个完整闭环；真实依赖、MySQL最小账本/幂等、trace |
| M09 | 同query对比Dense/BM25/Hybrid，不预设融合必胜 |
| M10 | 多问题、生命周期、缓存/outbox投影，缓存丢失不丢事实 |
| M11 | 交错后补充归属；澄清、更正、不串单 |
| M12 | 同一主流程完整聚合/级联；主分类一次，缺槽位禁工具 |
| M13 | 真实FastText训练/推理/量化，小样本只报告实际效果 |
| M14 | SOP版本/静态校验/不同分支；15+覆盖分批扩展 |
| M15 | 持久审批/唯一领取/对账，重启重放不重复效果 |
| M16 | 意图/多轮/流程视图和事实回复；只读版不依赖M15 |
| M17 | 独立冻结评测、增量对照；evaluation_cli audit/run，500+规模另列状态 |
| M18 | 采集→审核→三路更新→回归/回退；发布加固单做 |
| [M19](MODULES/M19_STRUCTURE_LEARNING.md) | 已完成运行源码分层、独立工具/demo安装与离线机制导航；[安装/离线交接](../handoffs/M19.md) |
| [M20](MODULES/M20_BUSINESS_SOURCE.md) | MySQL记录/维护API与Business MCP，然后Retrieval MCP；两个独立服务/顺序特性，保留事务边界 |
| [M21](MODULES/M21_OBSERVABILITY.md) | 标准OTel/跨MCP/LangGraph/Saver观测，然后Collector/Tempo/Prometheus/Grafana集成；两个顺序特性 |
| [M22](MODULES/M22_LEARNING_WORKBENCH.md) | 主工作台、MCP/工作流学习、意图/检索/记忆实验、评测飞轮；四个顺序特性 |
| [M23](MODULES/M23_PERSONAL_DEPLOYMENT.md) | 已完成Linux3.14.7/容量、保签名资产绑定、私有浏览器远端闭环、隔离恢复和安装回滚；[交接](../handoffs/M23.md) |

M02–M18原核心能力及其历史证据保留。M19→M20→M21→M22→M23个人学习路线已完成；旧M19部署/故障要求的必要部分归M23，旧M20物流、M21框架对照从当前路线撤下，历史由Git追溯。每M可有多个顺序特性，完成后停止，不自动启动业务扩展。

## 现代技术路线（M25–M31，规划）

用户明确要求保留当前main整套MySQL/OTel实践与部署入口，后续学习更活跃的Agent技术栈。旧代码与历史由bak/mysql-otel保留，main继续开发。此路线复用全部有效机制，以维护责任/可调试性选择工具，不以名字或热度证明效果；它是用户工程扩展，不改写M00–M23历史来源和验收。

选定**全部应用关系数据迁PostgreSQL + 官方AsyncPostgresSaver + Langfuse原生Python SDK**。实际LangGraph审批图使用官方callback，自定义13段/httpx模型网关显式接原生SDK。Langfuse元数据库与应用事实库分离，可共用PG实例；业务/outbox事务不跨Langfuse。Redis/Milvus/etcd和基础指标Prometheus/Grafana保留。原独立Collector/Tempo与MySQL仅在M30满足功能/数据/恢复条件后退役；SDK内部OTel/W3C上下文继续使用。LangSmith不作为此轮主平台，托管需求以后另评估。

备份分支bak/mysql-otel已推送并固定在`930958a2f21bab7377c30c74ca770771fa1004cf`，保留M00–M23、Linux CI修复及原MySQL/OTel部署恢复入口；后续提交不合入该分支，不移动/覆盖它。继续遵守AGENTS：从同步main建立feature、特性分支验收、合入/推送main、同步六SHA，最终回到干净main。本轮计划由用户明确授权直接提交/推送main；备份动作已完成，不再为它建立独立模块或修改默认分支。

| 模块 | 用户可启动的首特性 | 后续范围/前置 |
|---|---|---|
| [M25 PG事实](MODULES/M25_POSTGRES_FACTS.md) · [计划](../planning/M25/PLAN.md) | 首项已交付；[协议/盘点交接](../handoffs/M25.md) | 全持久迁移/导入/隔离恢复待用户启动；M26前置仍未满足 |
| [M26 官方checkpoint](MODULES/M26_OFFICIAL_CHECKPOINTS.md) · [计划](../planning/M26/PLAN.md) | 官方保存器与旧状态转换、独立进程恢复 | M25全持久特性；探针后正式装配/完整历史恢复 |
| [M27 Langfuse平台](MODULES/M27_LANGFUSE_PLATFORM.md) · [计划](../planning/M27/PLAN.md) | 版本/自托管SeaweedFS、Web/Worker实际存入/读回 | M25/M26；无需付费对象存储，随后组合容量/平台恢复 |
| [M28 原生观测](MODULES/M28_NATIVE_OBSERVABILITY.md) · [计划](../planning/M28/PLAN.md) | 跨MCP、官方图与pause/resume可查关联 | M26/M27；随后业务覆盖和原工作台查询替换 |
| [M29 LLM工程](MODULES/M29_LANGFUSE_ENGINEERING.md) · [计划](../planning/M29/PLAN.md) | 现有报告/冻结集的Dataset/Experiment/Score映射 | M28；可选，随后Prompt版本/审核映射，不替换M18 active |
| [M30 切换与退役](MODULES/M30_MODERN_CUTOVER.md) · [计划](../planning/M30/PLAN.md) | 直接Prometheus指标与新部署/隔离恢复演练 | M25–M28；另启动最后快照/单写切换/旧服务退役，M29可跳过 |
| [M31 Reranker](MODULES/M31_RERANK_INTEGRATION.md) · [后续计划](../planning/M31/PLAN.md) | 首项已交付；[端口/内容交接](../handoffs/M31.md) | 同链质量对照待启动；随后Retrieval MCP/学习页/正式发布，不依赖迁库完成 |

每次只启动当前M的一项特性；先读规格/PLAN/直接handoff和当前代码，探针结果出来前不把未来范围当成已经可执行的详细实现。依赖未交付标BLOCKED，SDK/实库/容量未测不是PASS。M25后续迁移特性和独立M31同链接入/受控对照可启动，用户任选其一，不并行写共享网关/根锁；首试验通过不等于全库迁移或Rerank主链已交付。完成一个特性后停止，不能自动串行实施这七个M。

实施Prompt可直接使用：“实施M25全持久迁移特性。按docs/MODULES/M25_POSTGRES_FACTS.md、planning/M25/PLAN.md和handoffs/M25.md完成全持久适配、只读快照导入/续跑与隔离PG实际备份恢复；旧路线保留在bak/mysql-otel，按AGENTS从main建特性分支并合入main。一个主写者，验证/中文提交/推送/同步后停止，不切现网、不卸载、不购买资源，不启动M26。”后续选择对应首特性，并核对实际前置。

补检索能力下一项可使用：“实施M31同链接入与受控对照特性。按docs/MODULES/M31_RERANK_INTEGRATION.md、planning/M31/PLAN.md和handoffs/M31.md，在召回后、意图归并前复用已验RerankPort；固定同候选/同向量，dev确定instruct/预算/失败策略，独立test验收排序与拒识。保留active发布；按AGENTS验收/中文提交/推送/合并/同步后停止。只做本特性，不同时迁数据库或部署Langfuse；后续真实会话/MCP/学习页与正式发布另启。”

容量与资源：现有约7.51GiB适合先做受控低负载试验，不保证完整Langfuse与训练峰值共存。当前Langfuse需Web/Worker/PG/ClickHouse/专用noeviction队列Redis/S3对象存储；不能照旧截图写ClickHouse或对象存储可选。选官方支持的SeaweedFS单机自托管，使用现有磁盘/容器网络，和PG/ClickHouse一起做容量及隔离恢复实测；不购买COS、RDS或其他托管存储，不默认安装已归档社区MinIO。容量不足则报告，不自动开通计费资源。

规划交付只改文档/PLAN和明确授权的Git引用/提交，不创建空handoff，不改变当前数据库、服务、锁或发布资产。用户本轮明确授权备份旧分支及提交/推送计划；它不启动任何迁移、平台部署或Rerank实施。事实进度只写STATE；M25–M31 handoff均待实际实施产生。

## 学习与运行方式

以下段落描述已交付的MySQL/OTel路线；未来main演进目标由M25–M31及ADR024/025单独定义。它们不是新路线应当继续保留MySQL/Tempo的实施指令。

保留一个工程和一套引擎。模型执行方式、业务数据源、运行地点分别选择：离线练习/CI不联网；demo只在显式选择或导入时使用样例；个人运行从MySQL读取可维护记录并显式调用真实模型。合成内容可以是真实持久业务记录，是否连接数据库/协议/模型与内容是否来自企业分别说明。服务器位置不自动决定联网模式，数据库失败不回退JSON。

M22按机制组织页面与操作教程，不复制引擎；页面浏览/历史/说明不消耗模型调用，明确点击真实执行才进入现有预算。观测只解释实际结构化决策/证据，不采集隐藏推理。M20维护资料不等于会话业务写工具，synthetic_rights不代表实际订单/券变更。

用户追加协议/RPC学习目标：M20以独立Streamable HTTP Business/Retrieval MCP服务复用核心能力，分别观察schema/可信上下文/双端校验/数据库结果。事务账本、outbox和MySQLSaver不为拆服务而全部搬成MCP；不提供任意SQL/Redis/Milvus/etcd操作。现有MCP正式SDK/stdio本来真实，合成的是后端事实；M15实际审批图/checkpoint已有，整个13段主链不冒称全由LangGraph实现。

观测已接入OpenTelemetry SDK/Collector、Tempo单体/本地存储、Prometheus、私有Grafana；跨MCP/实库/图恢复、受保护查询、看板/重启与导出故障通过，见M21交接。Langfuse的LLM专项学习价值保留，首轮不共置另一套web/worker/数据库/对象存储。真实完整组合和索引/训练峰值仍须测量；不买资源或降低核心能力换通过。

### 顺序实施指令

下列指令每次只选一条；实施前按当前规格/直接handoff核对接口与前置、写或更新当前M短PLAN，按AGENTS单特性交付，完成后停止。

这是依赖与推荐顺序，不要求等待M22所有页面才可远端使用：M20/M21和M22主工作台通过后，可由用户单独启动M23的首个远端版本，未实现tab明确标注；其余学习页面随后逐特性迭代。不会因前置满足自动启动部署。

1. **M19结构：** 已完成，现行接口与证据见`handoffs/M19.md`；不重复实施。
2. **M20业务服务：** 第1特性已完成，接口与最终证据见`handoffs/M20.md`：MySQL订单/券/活动、维护API、独立Business MCP及应用接入；新订单真实内容、更新后新消息、独立重启、越权与零调用重放已验收。
3. **M20检索服务：** 第2特性已完成，证据见`handoffs/M20.md#独立检索服务`。正式Streamable HTTP、原检索Port远端适配器、双端schema/语义/归属与累计预算回执已接通；真实同向量Dense/BM25/Hybrid对照、断连/独立重启及跨两个MCP的客诉共11项通过。两项特性结束，不自动启动M21。
4. **M21标准观测：** 第1特性已完成，实际SDK/Collector、13段与两MCP/实库、图/Saver、跨进程恢复Link及状态/脱敏/重放反例通过，见`handoffs/M21.md`；未建设前端。
5. **M21观测平台：** 第2特性已完成，Tempo/Prometheus/私有Grafana真实存储/同trace查询、受保护分页、12面板、四组件独立重启及两类导出故障/资源已验，见`handoffs/M21.md`。只在隔离目录增量启动观测栈，应用未迁移，既有中间件未改变。M21结束，等待用户启动下一特性。
6. **M22主工作台：** 第1特性已完成，登录、主对话/明确补槽、MySQL分页历史、原事实/证据/时间线、订单/券/活动及关系维护已实际浏览器连两个MCP验收；见`handoffs/M22.md`，未部署。
7. **M22协议与工作流：** 第2特性已完成，实际MCP schema/双端校验、真实业务查询、SOP与合成审批图/checkpoint浏览器练习通过；效果1/执行1、重放和跨身份守卫通过。
8. **M22意图检索记忆：** 第3特性已完成，纯规则原文/否定/更正练习、原run意图/归属投影、同向量Dense/BM25/Hybrid、MySQL/Redis/outbox实际状态及同run Tempo/Grafana关联已浏览器验收；浏览0模型。
9. **M22评测飞轮：** 第4特性已完成，受控历史报告/差例、独立采集/人工审核、候选与完整版本门禁、显式串行任务取消/硬中断/恢复已浏览器验收；实际离线四路PASS，旧清单/过期审核与不足预算按实拒绝，active未变。M22四项完成后停止；M23须另行启动。
10. **M23远端：** 已交付一个云端应用、两个独立HTTP MCP与原观测栈；Linux3.14.7、三包/FastText、保签名工件绑定和新完整内容发布已验。本机业务服务关闭后，浏览器新订单、四轮补槽/更正、历史/同trace、独立重启、鉴权/越权/schema及零调用重放通过；实际备份恢复27表并由隔离应用查询/回读，安装v1→v2→v1和短时索引/查询/训练容量通过。两个安装版本的业务wheel相同，不宣称历史代码升级或跨schema回退；私有SSH入口，无公网TLS/长期SLA/高并发结论。见`handoffs/M23.md`；完成后停止。

## 按影响选择验收

普通回归默认离线；真实模型验收按变更影响选择；完整真实评测按明确目标单独启动。所有检查在特性分支完成，main仅合并、保存和同步。表内是选择依据，具体样本及运行上限在本次验收前说明，不设永久统一token/金额限额。

| 改动 | 适用离线检查 | 针对性真实范围 | 扩大覆盖条件 |
|---|---|---|---|
| 文档、目录、缓存清理、日志轮转、内部整理 | 链接/资源/导入/构建及相关回归 | 无；没有模型行为变化不调用API | 请求、解析、决策或业务效果实际变化 |
| 类型/权限/归属守卫/幂等/金额/证据/预算/超时/取消 | 固定动作与反例、状态/并发/故障/解析/编排 | 仅确需实库/协议的部分，模型可为0次 | 模型解释、归属策略或内容路径也变化 |
| 回复措辞、Prompt/schema或输出解析 | 模板/schema/异常解析/事实守卫 | 改变内容、关键反例及必要相邻输入；不默认重验embed/FastText/事件/审批 | 共享核心Prompt/最终事实生成广泛受影响 |
| 分类/实体提取/工具选择 | 冻结反例、来源/缺槽位/未知项、工具白名单 | 受影响意图/字段/工具路径，保留本次真实内容证据 | 共享策略/模型或多条消费路径改变 |
| 检索/Embedding/FastText/训练资产 | split/签名/近义泄漏/数值/清单/回退 | 受影响索引和查询；Embedding签名变更须新集合/reindex及内容效果 | 共享语料/词典/主模型切换，扩大代表性业务覆盖 |
| 审批领取/对账/恢复/数据库协议 | 固定动作的身份/参数/CAS/UNKNOWN/幂等/故障 | 受影响持久协议/下游/重启；只有模型生成计划/最终事实变化才加模型样本 | saver/下游协议或版本变化；企业接口另验 |
| 明确启动质量评测、发布或多组消融 | 冻结审计、版本/配置/资产及确定性门禁 | 明确数据/组数的完整评测或发布范围 | 大批量付费先列调用/token范围、共享上限和必要性并授权 |

相关入口支持按能力/样本/模式选择，见[应用README](../modules/deephelp-app/README.md#按改动选择验收)。同一事实路径优先以M17选定模式/会话作为内容权威入口；仅在缺少所需能力证据时加M03对应能力，不再叠加M08/M12/M14/M16/M18相同路径。审批故障用固定动作区分协议与模型内容。失败及后续修改只使受影响证据失效；模型/endpoint/协议/相关配置及验证目标未变可复用同会话能力证据，hash一致仍需确认行为适用。完整版本发布保留现有成功报告与不可变清单守卫，普通工具整理不隐式启动发布或生成新模型评测。

## 验证与交付

Windows从根使用本机已验证的py -3.14 -m uv，应用仍为3.14.7；其他机器核对根pyproject要求。以下检查和适用的真实验收只在特性分支执行：

业务启动用 `python -m deephelp_app serve --mcp-module deephelp_tools.demo.mcp_server --corpus demo/data/m09_corpus.jsonl`，参数见[业务入口](../modules/deephelp-app/README.md#业务启动入口)。学习实验/fake在learning，显式探针在probes，冻结评测/训练选参在evaluation；`evaluation.evaluation_cli audit/run`使用冻结数据。包内运行策略、SOP/模板、合成业务事实、冻结集和学习数据分别按[资源职责](../datasets/README.md)读取。默认业务启动不依赖探针、学习工具或dev/test数据；完整发布准备时仍需要精确内容评测，不能用移目录绕过hash门禁。

```powershell
py -3.14 -m uv run --locked ruff check conftest.py modules
py -3.14 -m uv run --locked ruff format --check conftest.py modules
py -3.14 -m uv run --locked mypy
py -3.14 -m uv run --locked pytest <本次相关测试路径> --tb=short -q
git diff --check
```

检查命令按影响选择，上述不是每次小改动全部必跑的清单。共享装配/跨包/共享策略影响广时可做完整离线pytest；不机械每次全量，也不靠删有效测试省上下文。测试进程本身的本地计算与Agent读取输出/代码分别处理：成功看摘要，失败读必要诊断，不把近千条通过输出和所有测试代码注入会话。现有收集前网络守卫与CI离线保留，子进程等新传输边界须明确核验。

按上表另跑特性必要验证：文档查链接/命令，接口回归消费者，真实依赖通过专用探针和路径。M01的--live只检查配置，不是模型验收；纯离线改动不强制联网。用户2026-10-03更新交付约定：特性验收通过后直接合并/推送main，不再main复验；main仅保存与同步代码。历史文档中的main探针示例是可选运行入口，不构成交付要求。Git冲突、push失败、SHA不一致仍按AGENTS停止报告。

| 任务 | 验证边界 |
|---|---|
| 类型/fixture/文档 | 离线，不消费额度 |
| 模型/提取/SOP | 仅行为受影响时选择针对性真实接口/内容；完整评测单独启动；模型使用与恢复遵循AGENTS |
| MySQL/Redis/Milvus | 先只读连接，再用特性专用表/前缀/集合，不重建P00 |
| MCP/合成下游 | 协议真实，业务合成；离线注入不替代实时成功 |
| 重启/删除/恢复/故障 | 现有DeepHelp专用云实验环境已授权；核对项目目标与隔离范围，按AGENTS执行 |

失败报告、Git交付遵守AGENTS。PLAN写步骤，handoff写证据；不复制命令日志和会话过程。历史资料仅按交接入口追溯。

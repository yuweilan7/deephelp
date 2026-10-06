# DeepHelp 路线图

本页是能力目录和验收导航，不放执行队列、当前安装或历史实施Prompt。已交付范围/限制只见[PROJECT_STATE](PROJECT_STATE.md)，任务范围/Git流程见[AGENTS](../AGENTS.md)，当前命令见[LOCAL_SETUP](LOCAL_SETUP.md)及包README。

复刻PDF的核心机制，并以一套Python/Agent引擎支持本地原理学习和云端完整教学；不新增企业业务、全栈本机要求或自动跨M调度。M编号是能力目录，规格内可以有多个顺序特性；已完成模块的规格用于说明机制和回归目标，后续变更从真实缺口续做。

## 来源到能力

| PDF文件页 | 机制 | 模块与工程替代 |
|---|---|---|
| 15–18 | 分层、模型适配 | M01/M03/M08；Python替代Spring |
| 19–24 | 清洗、实体提取 | M04；Regex+受限API，保留否定/尾订单/来源 |
| 25–35 | 规则→召回→记忆→兜底 | M02/M05/M09/M12/M13，阈值由dev校准 |
| 35–41 | Dense/BM25/融合 | M05/M09；API/FP32不是BGE-M3同空间 |
| 41–54 | FastText、反馈 | M13/M17/M18；独立样本，候选审核 |
| 55–61 | 聚合、13段 | M10–M12；只有600进入主意图服务 |
| 62–66 | 记忆、生命周期 | M08/M10；事实库与Redis/Milvus职责分开，当前PG替代原MySQL |
| 67–69 | SOP/MCP/ReAct/输出 | M06–M08/M14/M16；真实协议+合成下游，写操作需M15 |
| 70–76 | 调试、评测、分期 | M08/M16–M18；实际覆盖与规模分开 |

内部后台/京ME、全供应商及本地大模型不默认逐项建设。多Agent/Skills等由用户2026-10-05明确纳入后续M40–M46规格；M40已交付、M41能力/安装已验但本地清理受工具拒绝、M42能力/安装与恢复已验但本地清理受工具拒绝、M43三项作者/预演/审核发布、M44三项语料作者/索引诊断/反馈发布及各自精确安装恢复已验；M45–M46仍待实施，具体协议是本项目设计，不宣称PDF已经给出完整实现。M19–M23及M25–M31为用户2026-10-04指定的个人学习/工程扩展，Rerank已另立M31，不宣称来自PDF原实现。细页码见 [SOURCE_MAP](SOURCE_MAP.md)，裁决见 [DECISIONS](DECISIONS.md)。

## 能力与退出条件

| 规格 | 能力目标 |
|---|---|
| [M00](MODULES/M00_ARCHITECTURE.md) | 架构/语义设计与来源裁决 |
| [M01](MODULES/M01_PYTHON_ASYNC_SKELETON.md) | 应用生命周期、异步/共享资源与离线骨架 |
| [M02](MODULES/M02_CONTRACTS_DATA.md) | 唯一DTO/状态/错误/合成固定样本 |
| [M03](MODULES/M03_MODEL_GATEWAY.md) | 模型网关、schema/tool守卫、Embedding签名/共享预算 |
| [M04](MODULES/M04_TEXT_ENTITY.md) | 原文/实体/否定/更正与证据来源 |
| [M05](MODULES/M05_DENSE_INGEST.md) | 版本化语料、真实Dense索引与独立dev评测 |
| [M06](MODULES/M06_MCP_MOCK.md) | 正式SDK stdio发现/调用与合成只读工具 |
| [M07](MODULES/M07_SOP_MINIMAL.md) | 版本化SOP、有界工具循环与事实守卫 |
| [M08](MODULES/M08_MVP_INTEGRATION.md) | 三类单消息13段闭环/账本与幂等 |
| [M09](MODULES/M09_HYBRID_RETRIEVAL.md) | 中文BM25、Dense及融合的同条件对照 |
| [M10](MODULES/M10_MEMORY_LIFECYCLE.md) | 多问题生命周期、持久账本/outbox与可重建投影 |
| [M11](MODULES/M11_EVENT_CLUSTER.md) | 有界候选/硬约束/事件归属与更正 |
| [M12](MODULES/M12_CASCADE_PIPELINE.md) | 600主级联与聚合后的完整主链 |
| [M13](MODULES/M13_FASTTEXT.md) | FastText可选训练/推理/量化及实际效果 |
| [M14](MODULES/M14_SOP_GOVERNANCE.md) | SOP版本治理、分支与业务覆盖 |
| [M15](MODULES/M15_DURABILITY_APPROVAL.md) | 持久审批、唯一领取、UNKNOWN查询对账 |
| [M16](MODULES/M16_OUTPUT_DEBUG.md) | 事实回复与同源意图/多轮/流程视图 |
| [M17](MODULES/M17_EVALUATION.md) | 冻结评测、相邻/完整对照与质量边界 |
| [M18](MODULES/M18_DATA_FLYWHEEL.md) | 独立采集/审核/更新及完整发布/回退 |

## 个人运行（M19–M23）

| 规格 | 能力目标 |
|---|---|
| [M19](MODULES/M19_STRUCTURE_LEARNING.md) | 运行/工具/demo分层与独立安装 |
| [M20](MODULES/M20_BUSINESS_SOURCE.md) | Business维护/查询及独立Retrieval MCP |
| [M21](MODULES/M21_OBSERVABILITY.md) | 标准观测/跨进程关联；旧平台历史范围 |
| [M22](MODULES/M22_LEARNING_WORKBENCH.md) | 对话、协议、检索/记忆、评测飞轮四项工作台 |
| [M23](MODULES/M23_PERSONAL_DEPLOYMENT.md) | 旧路线私有云安装、容量、备份/恢复 |

## 现代技术路线（M25–M31）

| 规格 | 能力目标 |
|---|---|
| [M25](MODULES/M25_POSTGRES_FACTS.md) | 关系事实统一PostgreSQL，迁移/恢复 |
| [M26](MODULES/M26_OFFICIAL_CHECKPOINTS.md) | 官方AsyncPostgresSaver及原历史/审批恢复 |
| [M27](MODULES/M27_LANGFUSE_PLATFORM.md) | Langfuse自托管/SeaweedFS/容量与隔离恢复 |
| [M28](MODULES/M28_NATIVE_OBSERVABILITY.md) | 原生SDK/图callback及业务观测/页面授权 |
| [M29](MODULES/M29_LANGFUSE_ENGINEERING.md) | 历史评测映射、不可变Prompt及显式annotation审核 |
| [M30](MODULES/M30_MODERN_CUTOVER.md) | PG单写、新平台正式安装与旧服务退役/恢复 |
| [M31](MODULES/M31_RERANK_INTEGRATION.md) | Rerank专用端口、同候选评测及主链/学习/完整发布 |

## 可读性与学习体验（M32–M36）

| 规格 | 能力目标 |
|---|---|
| [M32](MODULES/M32_CODE_ORGANIZATION.md) | 唯一类型/职责、适配器/装配/工具、测试与清理导航 |
| [M33](MODULES/M33_JAVA_LEARNING_COURSE.md) | Java读者的14章课程、原引擎离线实验及中文讲解 |
| [M34](MODULES/M34_PERSONAL_ACCESS.md) | 个人直接进入及服务端安全/归属保持 |
| [M35](MODULES/M35_FRONTEND_FOUNDATION.md) | Vue工作台、原能力迁移与同源正式安装 |
| [M36](MODULES/M36_EXPLAINABLE_WORKBENCH.md) | 原run确定性解释、机制/观测及完整教学场景 |

## 最终学习收尾（M37–M39）

| 规格 | 能力目标 |
|---|---|
| [M37](MODULES/M37_LOCAL_CORE_LEARNING.md) | 无旧状态本地核心路径与云教学入口 |
| [M38](MODULES/M38_PYTHON_AGENT_READABILITY.md) | Python/Agent关键契约/时序中文讲解及职责收口 |
| [M39](MODULES/M39_FINAL_FILE_CLEANUP.md) | 有用途/引用证据的受控与忽略/临时文件清理 |

旧M24冻结路线已撤下，代码备份为`bak/mysql-otel`；有效旧安装/数据/恢复资料仍按消费者保留。M25–M30替换后端/观测，不改变领域/授权/审批权威；M31主链已接入，但收益证据不足以设为默认开启。M32–M39复用同一引擎和现有课程，所有已交付结果见STATE，不把旧首特性提示作为新的启动限制。

## 后续能力规格

M40–M46从M39的已交付源码/学习基线增量扩展；M40–M44的能力、验收及M41/M42剩余收尾见STATE，M45–M46仍待实施。原讨论中的独立“全量注释M40”取消；详细Python/Agent注释、相关职责整理、课程/前端学习与真实观测、相关文件清理成为[每个feature的交付习惯](../AGENTS.md#每个feature的编码与交付习惯)。M38/M39保留历史成果；不在新增能力后重复安排全项目大整理。

| 规格 | 三个顺序feature的能力结果 | 直接依赖/可独立性 |
|---|---|---|
| [M40](MODULES/M40_MULTI_AGENT_COLLABORATION.md) | 受控委托与首条协作路径；有界协作/恢复；真实业务协作与发布 | 现有会话/SOP/MCP/审批/预算；不等待Skills，不新增另一套主分类/事实账本 |
| [M41](MODULES/M41_AGENT_SKILLS.md) | 版本化可执行Skill；受控组合；Skill管理/选择与发布 | 复用现有SOP/工具即可开始；只有委托类Skill依赖已验M40接口 |
| [M42](MODULES/M42_BUSINESS_CATALOG_EXPANSION.md) | 可扩目录与首条新查询；两类新增客诉闭环；版本兼容与新目录发布 | 现有三意图/Business MCP/目录与发布；不强依赖M40/M41 |
| [M43](MODULES/M43_VISUAL_SOP_AUTHORING.md) | 已验收：可视草稿/CAS/校验；原执行器受控预演；不可变审核/完整发布/精确恢复 | 复用M42六叶子及M41批准Skill；原图仍为lookup/branch/end，无独立Skill图节点 |
| [M44](MODULES/M44_CORPUS_WORKBENCH.md) | 已验收：PG语料作者/导入审核；冻结候选/同向量诊断；人审反馈/分类与完整发布/精确恢复 | 正式Hybrid绑定门禁、诊断退化保留；合成固定集分类持平，未证明客户泛化，Rerank未配置 |
| [M45](MODULES/M45_INDEPENDENT_QUALITY_VALIDATION.md) | 独立盲测集；精确版本对照；审核改进/留出集复验 | 可基于现有引擎独立开始，不等所有新M；每次比较只使用已交付且冻结的能力/资产 |
| [M46](MODULES/M46_LONG_TERM_MEMORY.md) | 可追溯跨会话召回；冲突/遗忘控制；生命周期与发布 | 现有记忆/PG/outbox/归属；不等待多Agent，接入协作时再核对M40授权上下文 |

编号是导航顺序，不是强制串行依赖；通常从M40开始，每次按明确指定的M完成其全部未完feature后停止。实施前核对直接依赖的当前交接/接口，并按[PLAN格式](PLAN_MODULE_PROMPT.md)写本M短计划；本次规格交付不创建空handoff、不改调度或自动启动实施。固定评测样本在相关feature调参前冻结，不能等M45再补测；M45提供独立样本来源及系统性改进证据，不承担全仓库整理。

多模态、主动服务、本地模型/供应商路线本轮不纳入，也不预写对应远期M。PDF的业务泛化/解决率/SLA宣传不转为本项目已经达到的结论。

## 按影响选择验收

目的是真实证明变化，同时避免重复历史探针和无意义的远程消费。具体样本/运行上限由本次任务决定；验收在特性分支完成，main只保存同步。

| 改动影响 | 适用检查 | 必须真实验证的部分 |
|---|---|---|
| 文档、目录/缓存/过程副本、等价整理 | 链接/引用，受影响导入/CLI/资源/构建与必要回归 | 无模型行为变化不调用API |
| 类型、权限/归属、幂等、金额/证据、预算/超时、取消/并发 | 固定动作、反例、状态/故障/重放与相关回归 | 只有必须证明持久协议/下游的部分；模型可以0次 |
| Prompt/schema、输出解析、回复措辞 | 模板/解析/事实守卫 | 改变内容、关键反例及受影响相邻输入 |
| 分类/实体/工具选择 | 冻结反例、缺槽/未知、来源和工具许可 | 受影响字段/意图/工具路径的本次真实内容 |
| 检索/训练/Embedding/Rerank资产 | split/签名/泄漏、数值/排序、版本清单 | 受影响索引/查询和代表效果；签名变更须新集合/reindex |
| 审批/领取/对账/saver/数据库协议 | 身份/参数/CAS/UNKNOWN、并发/取消/故障 | 受影响持久/恢复协议；固定动作不反复经过模型 |
| 明确启动完整质量评测/发布/多组消融 | 冻结审计、精确版本/配置/资产及确定性门禁 | 目标规定的完整范围；大批量付费按授权边界列具体计划 |

共享模型/核心策略或检索资产变化须评估广泛影响，扩大必要代表覆盖，不自动重跑全部历史模块。能力证据和内容证据各证明其范围；Mock、HTTP200、配置/健康检查、固定回放、协议恢复不冒充模型质量。选择样本、共享预算和证据复用遵守AGENTS，缺失/失败/中断/跳过如实记录。

## 验证与交付

按影响选用以下入口，不机械每次全跑：

```powershell
py -3.14 -m uv run --locked ruff check conftest.py modules infra
py -3.14 -m uv run --locked ruff format --check conftest.py modules infra
py -3.14 -m uv run --locked mypy
py -3.14 -m uv run --locked pytest <相关测试路径> --tb=short -q
py -3.14 -m uv lock --check --offline
git diff --check
```

共享装配/跨包/策略影响广时才做完整离线pytest；源码/打包资源变动才核对构建和独立安装。文档/过程文件只查直接消费者，不靠全量live抵消清理收益。普通pytest/CI的收集前网络守卫保留，只允许受控本机HTTP/stdio；`pytest --live`只检查配置，不解除网络隔离。

内容验收优先选一个权威入口：[应用按改动验收](../modules/deephelp-app/README.md#按改动选择验收)、[工具/冻结评测](../modules/deephelp-tools/README.md)、[独立MCP服务](../modules/deephelp-services/README.md)。相同事实路径不叠加历史探针；完整发布仍绑定精确运行/工具/资产和适用成功报告。

范围内检查失败先定位修复，Git问题自主恢复后继续；必须的验证及六SHA尚未通过不能报完成。无法恢复的真实外部依赖保留续跑状态并报告，具体协作规则只维护在AGENTS。

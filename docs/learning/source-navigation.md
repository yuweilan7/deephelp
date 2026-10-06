# 从一条消息找到代码

先顺着一次请求阅读，再进入具体机制；同一领域对象和同一会话引擎贯穿应用、MCP与实验。

首次读Python/Agent代码先按[14章Java课程](README.md)运行01章，再回来定位。await与资源对应04–07，检索/记忆/审批/观测/发布对应10–14；中文注释解释当前调用的拥有者、等待和不变量，详细原理只放课程。

## 消息与事实的调用路径

跨会话明确引用沿[LongMemoryService](../../modules/deephelp-app/src/deephelp_app/application/long_memory.py)与[原PG池适配器](../../modules/deephelp-app/src/deephelp_app/adapters/persistence/long_memory.py)，实验为`course long-memory`，讲解在[第11章](11-memory.md#两会话长期记忆实验m46)。它在当前事实回答后回查/引用合法历史，不进入活动归属或SOP参数；原run选择保存在ResponseEnvelope，浏览不重跑模型。

真实八场景/重启只读入口是[long_memory_delivery](../../modules/deephelp-tools/src/deephelp_tools/probes/conversation/long_memory_delivery.py)，精确绑定从[memory_binding](../../modules/deephelp-app/src/deephelp_app/adapters/releases/memory_binding.py)读策略/schema与原内容；[同会话复核](../../modules/deephelp-tools/src/deephelp_tools/probes/conversation/memory_content_review.py)只允许发布比较守卫变化，逐文件拒绝其他运行变化，保留原件及新复核链。部署与实际限制见[M46交接](../../handoffs/M46.md#f3-生命周期精确发布与两向恢复)。

1. [API](../../modules/deephelp-app/src/deephelp_app/api/app.py)接收不可信输入，从入口注入身份；正文或trace ID不能授权。
2. [Conversation.run](../../modules/deephelp-app/src/deephelp_app/application/conversation/orchestrator.py)是13段编排入口。先授权hint，查原receipt，再领取新消息；重投返回原结果，不重跑模型或工具。
3. [preparation](../../modules/deephelp-app/src/deephelp_app/application/conversation/preparation.py)在原子领取前加载窗口和判定归属；[state](../../modules/deephelp-app/src/deephelp_app/application/conversation/state.py)把实体、冲突、来源和固定版本推进到当前问题。一次请求拥有一个可变QuestionState，里面的Question仍是不可变DTO。每个await完成的状态即刻写入该请求对象，后续失败仍保留已经得到的诊断。
4. [事件归属](../../modules/deephelp-app/src/deephelp_app/application/event_cluster/aggregation.py)只决定归属；fragments分诉求，window裁剪窗口，components约束合并，judge调用结构化判断。主意图只在Conversation的600调用[IntentService](../../modules/deephelp-app/src/deephelp_app/application/intent.py)。
5. [SOPExecutor](../../modules/deephelp-app/src/deephelp_app/application/sop.py)检查版本、槽位与可信上下文后派发固定工具；[事实检查](../../modules/deephelp-app/src/deephelp_app/domain/checks.py)要求金额、事实和证据闭合，缺槽不得调用工具。
6. [terminal](../../modules/deephelp-app/src/deephelp_app/application/conversation/terminal.py)先保存原账本终态，再采集[诊断](../../modules/deephelp-app/src/deephelp_app/application/conversation/diagnostics.py)。短时保存任务由当前请求创建并等待；shield允许取消时等它落盘，不允许继续执行模型/工具。run退出时重置ContextVar，防止下一条消息借用原计数。

Conversation保留stage/prepare等原方法入口，绑定的是对应职责模块的函数。Python把类中的函数绑定到实例，调用时自动传入self；它相当于Java对象将调用委托给同一组组件，没有继承第二套业务引擎。需要查计数和取消时，直接看[tracking](../../modules/deephelp-app/src/deephelp_app/application/conversation/tracking.py)与[stages](../../modules/deephelp-app/src/deephelp_app/application/conversation/stages.py)。

读函数时先看中文契约，再把分支对应到实际输入：`confirmed`来自历史消息，`fields`限定提取字段，`context_intent`来自已授权旧问题，`budget`由本次请求共享。`model_copy`生成新DTO值，但不会重新跑校验；因此结果/终态边界的`model_validate`不能省掉。DTO的冻结也不意味着其中所有容器都变成深度不可变对象。

| 容易误读的选择 | 从哪里追踪及为什么 |
|---|---|
| 自动归属是否已经领取消息 | preparation最后的accept才原子领取；模型先给目标/期望版本，竞争失败者读原receipt，不能再进入600 |
| 缺槽和未知意图是否一样 | intent/cascade分别保留最终类别与槽位状态；有类缺槽可补充，无类不能凭相似样例猜工具 |
| gather失败后兄弟任务是否结束 | text_entity.process创建两个Task，并在finally取消未完成者、等待全部回收；Task不是线程 |
| tool_choice是否保证工具参数正确 | sop._loop/_flow继续核对动作数、工具名和expected，_lookup再校验派发ID及事实证据；请求约束不能替代返回守卫 |
| 图恢复是否重新执行整条消息 | approval.resume先授权/领取，同operation为UNKNOWN时先query；checkpoint缺口从账本恢复，原message重投仍只读receipt |
| 已取消是否意味着下游没执行 | 主链保留实际派发ID，审批保存UNKNOWN；terminal只等待短保存，不继续业务，恢复需对账 |

## 唯一类型的责任

独立质量学习沿 [independent_data](../../modules/deephelp-tools/src/deephelp_tools/evaluation/quality/independent_data.py) 的来源/盲标/冻结纯变换 → [independent_store](../../modules/deephelp-tools/src/deephelp_tools/evaluation/quality/independent_store.py) 的原PG池/短事务CAS → [原评测扩展](../../modules/deephelp-tools/src/deephelp_tools/learning/workbench/independent_quality.py) 的owner与不含答案的投影；离线入口 [independent_quality_demo](../../modules/deephelp-tools/src/deephelp_tools/learning/course/independent_quality.py) 共用核心，不复制分类引擎。

[domain/models](../../modules/deephelp-app/src/deephelp_app/domain/models/__init__.py)只显式导出原公开名字；定义按概念保存在同目录的具体模块，内部import直接指向它们。

| 要查的规则 | 定义入口 | 向下依赖 |
|---|---|---|
| 身份、状态、预算、金额 | common | Pydantic与标准库；没有连接或锁 |
| 原文、实体与来源 | entities、text | 公共值及意图目录 |
| 意图目录、最终选择与级联 | intents、intent_decisions | 实体、证据和实际retrieval结果 |
| Chat、Embedding、Rerank输入输出 | model_io | 公共值；不持有HTTP客户端 |
| 检索候选、签名与MCP回执 | retrieval、retrieval_protocol | 模型值、意图目录、请求和预算 |
| 问题、窗口与归属图 | conversation、memory、events | 实体、目录和固定版本 |
| 工具、SOP、审批与响应 | tools、sop、approval、responses | 参数hash、事实/证据、状态及固定版本 |
| 发布与业务维护 | release、business | 不可变清单与业务值；不负责装配或事务 |

从公共值向具体业务类型单向导入；responses最后组合请求结果，不要求其他DTO反向导入它。领域包的__init__保持原import API，类型对象与职责模块中的对象相同。

## 从用例进入子系统

上层边界仍是domain/application/adapters/api/bootstrap/resources；下面的目录承接具体依赖。配置选择与资源生命周期在bootstrap/runtime，入口参数在bootstrap/commands。导入这些模块不打开数据库、模型或子进程。

| 责任 | 实际入口 | 调用者与失败处理 |
|---|---|---|
| 模型网关 | [model_gateway/gateway](../../modules/deephelp-app/src/deephelp_app/adapters/model_gateway/gateway.py)、[validation](../../modules/deephelp-app/src/deephelp_app/adapters/model_gateway/validation.py)、providers、fasttext_runtime | 用例经Port调用；协议、schema、usage由适配器验证，AppError交给原用例终态处理 |
| PG事实 | [persistence/ledger](../../modules/deephelp-app/src/deephelp_app/adapters/persistence/ledger.py)、cases、history、postgres | 接收/终态事务和问题版本归PG；历史API只读原事实，错误不回退demo |
| 记忆与检索 | memory_projection/redis_memory、milvus_memory；[retrieval/milvus_hybrid](../../modules/deephelp-app/src/deephelp_app/adapters/retrieval/milvus_hybrid.py)、milvus_dense | 用例决定召回；适配器处理索引协议。投影可重建，失败不篡改原PG账本 |
| MCP | [mcp/business/tool_gateway](../../modules/deephelp-app/src/deephelp_app/adapters/mcp/business/tool_gateway.py)、business_client、mcp_protocol；mcp/retrieval；mcp/shared/network_mcp | 客户端、双端schema与可信委托各有入口；预算未知仍保留预留，业务结果由原事实/证据守卫处理 |
| 审批 | [approval/postgres_checkpoints](../../modules/deephelp-app/src/deephelp_app/adapters/approval/postgres_checkpoints.py)、approval_store、checkpoints、synthetic_rights | ApprovalService显式暂停/恢复；checkpoint不是账本，UNKNOWN先查询对账，普通新消息不批准旧操作 |
| 观测 | [observability/telemetry](../../modules/deephelp-app/src/deephelp_app/adapters/observability/telemetry.py)、native_observation、langfuse_query、trace、prometheus | 埋点和查询解释实际执行；原用例处理诊断缺失，不让平台失败改变业务副作用 |
| 完整发布 | [releases/release_assets](../../modules/deephelp-app/src/deephelp_app/adapters/releases/release_assets.py)、asset_integrity、release_store、runtime_assets、feedback_store | 运行装配校验不可变字节/引用/active CAS；tools只在显式准备时参与，旧清单不绑定新源码 |
| 运行装配 | [runtime/assembly](../../modules/deephelp-app/src/deephelp_app/bootstrap/runtime/assembly.py)、auth、budget、event_runtime、release_runtime | assembly拥有AsyncExitStack并逆序关闭依赖；auth选择原身份，budget在派发前保存累计预留 |
| 通用连接配置与容量观察 | [bootstrap/connections](../../modules/deephelp-app/src/deephelp_app/bootstrap/connections.py)、tools的[probes/retrieval/capacity](../../modules/deephelp-tools/src/deephelp_tools/probes/retrieval/capacity.py) | Redis/Milvus/旧源读取共用KEY=VALUE解析，导入不读密钥；SSH容量只供显式导入/候选准入，子进程由调用者回收 |
| 独立服务 | [Business API](../../modules/deephelp-services/src/deephelp_services/business/api.py)、repository；[Retrieval API](../../modules/deephelp-services/src/deephelp_services/retrieval/api.py)、backend；transport/http | Business负责关系维护/查询事务，Retrieval负责冻结语料检索/回执；共享传输只管会话、origin和认证挂载 |

tools/evaluation内部按retrieval、quality、feedback、releases、migration、platform归组；probes按models、retrieval、protocol、conversation、approval、releases、platform、migration归组。learning/workbench组合原协议/分析/评测路由；learning/replay、faults、fakes提供显式实验。正式app没有反向tools/services导入，业务装配仍复用同一组领域类型与用例。

进一步读复杂支撑代码时，把等待和提交对应到实际拥有者：

| 想弄清的时序 | 入口与观察点 |
|---|---|
| 一次模型纠正怎样计量 | gateway.chat共用原budget，至多一次纠正且累加两次实际usage；rerank把index绑回原文，embed按原输入还原位置，schema/原生动作仍由本地守卫验证 |
| Windows的PG等待发生在哪个线程 | persistence/postgres的PoolLoop.call把协程送到连接所属Selector loop，用wrap_future异步取回结果；取消后等驱动收口，PostgresPool.acquire退出先rollback再putconn。它不同于Java线程阻塞Future.get |
| checkpoint与业务账本怎样分工 | approval/postgres_checkpoints只包装官方saver；setup才持迁移锁，启动不自动升级。旧整数channel版本保持递增，批准/UNKNOWN/效果仍在原审批账本 |
| 观测为何不改变业务结果 | native_observation每次创建独立图handler，并在finally恢复Task上下文；telemetry.observation把原异常交还用例，返回的语义失败不计成HTTP成功，未知usage不填零 |
| 工作台任务完成是否表示质量通过 | learning/workbench/jobs的execute保存SUCCEEDED仅表示runner正常返回，PASS要看result；cancel持同一guard等待清理，SharedGate借用原预算writer。路由闭包只选择已允许的原入口 |
| 发布中断后怎样确认 | releases/release_store先验字节，再锁channel并CAS，原子保存active/previous和事件。commit后丢确认不能靠rollback撤销，恢复需读指针；退役仍须核对运行/审批引用 |

服务端MCP的工具描述、FastAPI的接口说明以及CLI读取的`__doc__`有运行消费者。因此本M在这些位置主要加普通注释，保留原协议/帮助字符串；不能为了翻译文档字符串而暗中改模型看见的工具描述。详细Java类比、反例和练习答案继续使用原课程，不新增一套教材。

原bootstrap/*_cli和tools下已有CLI名字是受支持的命令入口，文件只转调职责模块中的main，具体实现只有一处。消费者包括旧运行文档、部署start.sh、显式任务与历史恢复命令；这不是等待删除的临时别名。新实现路径同样支持明确执行，内部代码和patch定位直接使用新路径。Business/Retrieval/workbench原模块路径变成包，由__main__继续承接受支持的`python -m`命令。找逻辑时进入表中实现，不在薄入口增加业务代码。

## 找对应断言

三包测试复用根conftest的离线网络守卫；移动目录不创建另一套fixture或引擎。文件名中的M编号帮助追溯来源，目录表示现在验证的责任。

| 想验证什么 | 当前测试目录 | 保留的边界 |
|---|---|---|
| DTO、输入与工具契约 | app/tests/unit/contracts | 身份/金额/来源、字段校验、双端schema；各类型和各入口的反例分别保留 |
| 运行边界与模型请求 | app/tests/unit/runtime、model_gateway；tools/tests/unit/runtime | app独立导入/指纹/装配、协议解析/计量、远程隔离；原CLI与职责实现是同一个main |
| 会话、检索、记忆与审批 | app/tests/unit/conversation、retrieval、approval | 600调用次数、缺槽工具0、更正/归属、重投/取消、UNKNOWN与唯一执行 |
| 事实、观测与完整发布 | app/tests/unit/persistence、observability、releases | PG账本与投影、诊断缺失不影响业务、不可变资产和保留引用 |
| 冻结质量与反馈/恢复 | tools/tests/unit/quality、feedback、migration、platform、approval、learning | gold/heldout与审核隔离、迁移原状态、平台映射、工作台有限任务与取消 |
| 组件协作与MCP两端 | app/tests/integration、e2e；tools/tests/integration；services/tests/business、retrieval | 原事实路径、本机SDK协议、可信委托、共享预算、跨服务错误/重放 |

例如冻结样本标签检查与同样本的会话执行是不同断言：前者检查数据泄漏/来源，后者检查引擎事实和调用次数。此次审计保留这些独立用例；详细保留与实际删除依据见[M32交接](../../handoffs/M32.md)。

## 离线定位练习

从仓库根运行：

```powershell
py -3.14 -m uv run --locked pytest modules/deephelp-app/tests/integration/test_m12_pipeline.py modules/deephelp-app/tests/unit/runtime/test_m32_structure.py --tb=short -q
```

观察原测试中的缺槽→补槽、更正、重投、600计数和模型/工具调用断言，再在上述路径逐步找到实现。这里复用原离线引擎及合成事实，证明组织后的行为与边界，不代表远程模型质量或新云版本。旧完整发布绑定旧运行字节；本轮源码指纹变了，应保留原安装或在未来正式发布时重新取得精确内容证据。


M40显式协作从原[SOPExecutor](../../modules/deephelp-app/src/deephelp_app/application/sop.py)的lookup进入[CollaborationSession](../../modules/deephelp-app/src/deephelp_app/application/collaboration.py)，领域值在[collaboration](../../modules/deephelp-app/src/deephelp_app/domain/models/collaboration.py)，角色资产在[sop_data](../../modules/deephelp-app/src/deephelp_app/resources/sop_data/agent-policy-v2.json)。阅读顺序与离线实验见[09章](09-agent-basics.md#受控专业-agent委托m40)；协调者/专业角色不改变原600、工具事实与审批权威。

M41版本化Skill：[机器契约/执行](../../modules/deephelp-app/src/deephelp_app/application/skills.py) → [原SOP](../../modules/deephelp-app/src/deephelp_app/application/sop.py) → [09章实验](09-agent-basics.md#版本化业务skillm41)。

管理阅读：[候选/审核原件](../../modules/deephelp-app/src/deephelp_app/adapters/releases/skill_store.py) → [原工作台管理](../../modules/deephelp-tools/src/deephelp_tools/learning/workbench/skills.py) → [完整发布](../../modules/deephelp-tools/src/deephelp_tools/evaluation/releases/release_prepare.py)。管理API无模型，试运行复用原converse/course；生产装配在原LiveAssembly消费审核后的清单快照。
M43作者从[草稿事实源](../../modules/deephelp-app/src/deephelp_app/adapters/releases/sop_drafts.py)进入[作者API](../../modules/deephelp-tools/src/deephelp_tools/learning/workbench/sop_authoring.py)，图校验仍是原GovernedSOP；[09章实验](09-agent-basics.md#sop可视草稿与casm43)给出CAS、无效中间图及Java边界。前端WorkflowLab只生成契约，不执行业务判断。

M44沿[语料解析](../../modules/deephelp-app/src/deephelp_app/application/corpus.py)→[纯变换](../../modules/deephelp-app/src/deephelp_app/application/corpus_authoring.py)→[PG事务](../../modules/deephelp-app/src/deephelp_app/adapters/persistence/corpus_workspaces.py)→[作者API](../../modules/deephelp-tools/src/deephelp_tools/learning/workbench/corpus_authoring.py)阅读。[10章](10-retrieval.md#语料作者导入与审核m44)提供course corpus-authoring及Java/CAS边界；语料来源不等于业务事实权威。

冻结后沿[候选投影与同向量对照](../../modules/deephelp-tools/src/deephelp_tools/evaluation/retrieval/corpus_candidate.py)→原[DenseImporter](../../modules/deephelp-app/src/deephelp_app/application/dense.py)的pending journal→[HybridRetriever.compare_embedded](../../modules/deephelp-app/src/deephelp_app/application/hybrid.py)阅读。原评测LearningJobs拥有重任务，应用拥有累计预算与gateway；新增入口没有第二检索/教学引擎。
预演继续读[候选执行作用域](../../modules/deephelp-tools/src/deephelp_tools/learning/replay/sop_rehearsal.py)→原SOPExecutor→[持久任务及cancel/join](../../modules/deephelp-tools/src/deephelp_tools/learning/workbench/jobs.py)；course sop-rehearsal提供正常、缺槽和坏证据原结果。原observe_sop_node直接记录命中规则，不由页面重新判断。

审核接着读同一SOPDraftStore的review/approved→[原评测任务](../../modules/deephelp-tools/src/deephelp_tools/learning/workbench/evaluation.py)→[原完整清单准备](../../modules/deephelp-tools/src/deephelp_tools/evaluation/releases/release_prepare.py)与[发布字节守卫](../../modules/deephelp-app/src/deephelp_app/adapters/releases/release_assets.py)。review冻结完整闭包，RegistryStore只维护隔离候选版本索引，最终active仍由原ReleaseStore的PG CAS拥有。course sop-authoring同时展示字段diff、审核冻结和旧revision拒绝，远程调用0；真实内容沿catalog_probe借用当前累计writer。

## M42 新目录的阅读入口

M44发布从[原反馈导入与版本diff](../../modules/deephelp-app/src/deephelp_app/application/corpus_authoring.py)→[同观察最终分类/资产准备](../../modules/deephelp-tools/src/deephelp_tools/evaluation/retrieval/corpus_release.py)→[语料发布闭包守卫](../../modules/deephelp-app/src/deephelp_app/adapters/releases/corpus_binding.py)→原ReleaseManifest/ReleaseStore阅读。作者API借原PG池，LearningJobs拥有重任务，应用拥有累计writer；具体操作、Java类比和练习见[10章](10-retrieval.md#原反馈新版本与完整发布)。浏览/版本差异零模型，发布不能把语料当业务事实。

活动资格路径从[目录与版本](../../modules/deephelp-app/src/deephelp_app/domain/registry.py)进入：[600识别](../../modules/deephelp-app/src/deephelp_app/application/cascade.py)只接收当前scope的叶子，[SOP治理](../../modules/deephelp-app/src/deephelp_app/application/sop_governance.py)核对整份目录与工具许可，[Business PG](../../modules/deephelp-services/src/deephelp_services/business/repository.py)提供规则/资格/名额及原版本快照。学习用`course catalog`，机制和Java边界见[09章](09-agent-basics.md#版本目录与活动资格m42)。旧三叶子资源与参数hash保留，目录不通过放宽白名单扩权。

六叶子投诉继续读同仓储的_complaint_query和[新SOP](../../modules/deephelp-app/src/deephelp_app/resources/sop_data/business-registry-v4.json)，`course complaints`演示失败原因、等待、矛盾与已返还；[09章](09-agent-basics.md#下单失败与退款返券)解释尝试/退款/回退三个记录与Java类比。真实内容只由catalog_probe的complaints阶段证明。

M45配对阅读：[`paired.py`](../../modules/deephelp-tools/src/deephelp_tools/evaluation/quality/paired.py)固定单因素/完整分母/拒识/小样本统计 → [`paired_runtime.py`](../../modules/deephelp-tools/src/deephelp_tools/evaluation/quality/paired_runtime.py)先核对精确字节、共享Embedding、pending与完整结果零调用恢复 → [`paired_cli.py`](../../modules/deephelp-tools/src/deephelp_tools/evaluation/quality/paired_cli.py)拥有HTTP/预算writer；原evaluation队列借用gateway/预算，不能替代其拥有者关闭资源。实际分类仍回到原IntentService/Cascade；[`paired_quality.py`](../../modules/deephelp-tools/src/deephelp_tools/learning/course/paired_quality.py)复用评分器，离线不接触私有holdout。

M45改进沿 [`improvement.py`](../../modules/deephelp-tools/src/deephelp_tools/evaluation/quality/improvement.py) 的dev来源/事前标准→原Prompt候选→完整holdout核对与拒绝，接 [`improvement_store.py`](../../modules/deephelp-tools/src/deephelp_tools/evaluation/quality/improvement_store.py) 的PG顺序CAS/唯一holdout claim，再回原 [`release_prepare.py`](../../modules/deephelp-tools/src/deephelp_tools/evaluation/releases/release_prepare.py) 附加门禁；CLI拥有池、工作台借用池。课程 [`quality_improvement.py`](../../modules/deephelp-tools/src/deephelp_tools/learning/course/quality_improvement.py) 与[14章](14-evaluation-release.md#事前审核与一次留出集复验)共用守卫，解释零收益、关键退化与小样本拒绝，未建第二教学引擎。

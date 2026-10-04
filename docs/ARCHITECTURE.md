# DeepHelp 架构章程

版本：`design-v1.3`；语义契约：`0.1.3-draft`。这是 M00 工程设计基线，不是运行中系统描述。验收场景见 `ACCEPTANCE.md`，来源裁决见 `DECISIONS.md`，当前实现状态见 `PROJECT_STATE.md`。

## 1. 目标与边界

[源] 复刻意图识别、多轮事件归属、分层记忆、配置化SOP/MCP、调试与评测的核心机制。[设计] 加上可验证的权限、幂等、预算、持久审批和数据发布边界。不是把PDF做RAG问答，也不是复刻内部后台/登录/组织架构。

M08先交付优惠未享受、券不可用、订单活动查询等3–5个合成场景；M14扩展配置/场景，M17做冻结评测，M18做最小审核回流。2026-10-04用户将后续目标改为个人学习工作台：M19工程分层、M20持久业务、M21观测、M22教程/操作前端、M23云运行。该扩展复用核心引擎，旧物流迁移/框架对照撤下；真实支付/发券/退款与企业数据接入仍不在默认范围。15+情境和500+样本的历史证据与未完成项见STATE，不能用小样本外推质量。

## 2. 物理目录与依赖方向

[仓库] 根目录已是uv workspace、一个Git仓库与一个根uv.lock。保留现有infra；M01已创建业务包；后续增量复用，不按模块重建。当前实际文件见应用README。用户2026-10-04选定的新结构以[M19目录规格](MODULES/M19_STRUCTURE_LEARNING.md#目标与结构)为唯一目标：运行源码按职责分层，学习/探针/评测独立工具包，冻结集在datasets、演示在demo，不新增sops/evals/configs/scripts等重复入口。该迁移尚未实施；本章不复制第二套目标树。

依赖为 `application → domain`，`adapters → domain`，组合根装配两者；HTTP入口可调用application。领域不反向导入FastAPI、LangGraph或数据库SDK。初期单应用进程+一个本地MCP Mock即可，不按规划阶段拆22个部署单元。

只在真实变化点定义Port：ModelGateway（chat/embed可拆窄方法）、IntentRetriever、SOPExecutor、ToolGateway、Case/Message/Operation Repository。简单字段校验和规则函数不套接口工厂。事件裁决的模型适配可复用ModelGateway，不再建第二套模型基础设施。

用户追加的远端学习目标采用同仓库独立Business/Retrieval MCP进程，各复用领域/检索能力并通过原Port的HTTP客户端适配器接入。服务拆分不等于把MySQL账本/outbox/checkpoint事务搬成任意远程工具；内部事务保持当前权威边界。观测选OTel SDK/Collector→Tempo单体与Prometheus→Grafana，具体来源/双端校验和实际图/Saver范围见[M20](MODULES/M20_BUSINESS_SOURCE.md)、[M21](MODULES/M21_OBSERVABILITY.md)。这些是待实施目标，当前正式stdio及LangGraph审批/checkpoint的实现事实见STATE。

## 3. 主流程：13段，不等于13次模型调用

| 顺序/标识 | 职责 | 允许的停止路径 |
|---|---|---|
| 100 INPUT_VALIDATE | 参数、长度、请求标识及基础身份校验 | 非法输入统一错误；不创建业务case |
| 200 SAFETY_CHECK | 已验证身份上的权限/内容边界 | 无权限只写安全审计，不为他人建case |
| 300 TEXT_CLEAN | 保留原文，清洗/实体抽取，给话语功能标签 | 无可靠实体保留未知；不可猜测 |
| 400 BUILD_BIZ_CONTEXT | 读取受授权的有界历史/活动问题 | 普通有状态请求在MySQL不可达时失败关闭；仅明确无持久化的只读预览可只用当前消息 |
| 500 EVENT_CLUSTER | 有界候选、硬约束、补充归属、事实聚合 | 歧义则澄清，不强贴最近问题；M08显式passthrough |
| 600 INTENT_RECOGNIZE | 唯一一次进入主级联决策服务 | 规则/召回/增强/兜底均可拒识；内部尝试另计数 |
| 700 INTENT_CHECK | 注册表、可执行性、决策一致性 | unknown/无路由→澄清或人工 |
| 800 KEYWORD_CHECK | 已确认槽位及冲突校验 | WAITING_SLOT，禁止调用SOP业务工具 |
| 900 FETCH_SOP_ID | 固定SOP版本及工具许可集 | 无SOP/版本无效→人工，不编造流程 |
| 1000 SOP_EXECUTE | 有界ReAct/确定步骤、工具和证据 | 失败/预算耗尽/审批等待；写入前强校验 |
| 1100 REPLY_POLISH | 关键事实由结构化字段/模板输出；LLM只润色，不以另一LLM自评证明保真 | 模型失败回退事实模板，不新增业务事实 |
| 1200 SESSION_MAINTAIN | 业务状态/版本、记录、outbox提交 | 提交失败不能返回已解决 |
| 1300 RESPONSE_ENVELOPE | 统一结果、引用、预算与trace | 错误也形成可诊断响应 |

已接收业务请求的早停进入受控finalizer（1200/1300的适用部分），不能普通return绕开账本。非法/未授权请求只做统一错误与安全审计；业务数据库本身失效时诚实返回不可持久化，不能承诺强行保存。

只有600可调用主IntentCascade。500可进行补充归属裁决，1000可选择获准工具，1100可润色；这些均不得重新给主意图分类。600内部一次服务调用可有多个受预算约束的检索/模型尝试，trace分别计量。

## 4. 状态与入口

业务Question状态：ACTIVE、WAITING_SLOT、WAITING_APPROVAL、RESOLVED、HANDED_OFF、CANCELLED。Run状态：RUNNING、WAITING_APPROVAL、SUCCEEDED、FAILED、CANCELLED。响应outcome与业务状态分开：ANSWERED、CLARIFY、PENDING_APPROVAL、HANDOFF、REJECTED、ERROR。

```text
新消息 → 授权/幂等 → 新run → 选question或新建 → 处理
ACTIVE ↔ WAITING_SLOT                 # 仍未解决，当前run正常结束
ACTIVE → WAITING_APPROVAL             # 对应run可持久等待，M15前禁用写入
ACTIVE → RESOLVED / HANDED_OFF / CANCELLED
WAITING_APPROVAL → ACTIVE/RESOLVED/HANDED_OFF/CANCELLED
RESOLVED + 普通相似消息 → 新question   # 默认不隐式重开；显式重开另有版本审计
```

开放question包括ACTIVE、WAITING_SLOT；WAITING_APPROVAL仅供受控上下文读取。一个session可有多个活动question；同订单也可能有多个不同问题，不能用order_id唯一划分case。路由不确定时先保存受授权消息并返回CLARIFY，question_id允许为空；不得把歧义消息写入随机case。等待槽位不使用LangGraph interrupt，不无限挂起执行资源。

普通消息入口与审批恢复入口分开。审批只恢复原run；thread_id由服务端以workflow namespace+run_id映射，tenant作为隔离命名空间的一部分；不能信任客户端提交任意thread_id。审批绑定operation、参数hash、SOP版本、question版本、审批主体和期限；领取前绑定计划变化使旧批准失效。

批准CAS与唯一执行领取分责。领取绑定领取前计划，自身状态推进不使本次领取失效；外部更正需事务协调，已发出效果先对账。同一决定重投鉴权后复用结果/进行中引用，不重复工具或新建业务run。完整绑定、状态与回放规则只维护在CONTRACTS；裁决依据为ADR018。

## 5. 数据所有权与一致性

| 数据 | 权威 | 派生/缓存 | 写入者 |
|---|---|---|---|
| message、question、实体更正及版本 | MySQL | Redis窗口；Milvus摘要 | intake/业务用例 |
| operation、审批、执行结果证据 | MySQL | 可选只读缓存 | Operation用例/工具执行边界 |
| SOP/Prompt/registry发布版本 | 已发布文件及版本manifest；运行记录固定引用 | 进程缓存 | 指定发布者 |
| outbox事件与投影消费进度 | MySQL | Redis/Milvus目标结果 | 事务内产生，轻量worker投影 |
| 意图语料 | 有来源和split的版本化数据资产 | Milvus索引 | 导入/发布用例 |
| checkpoint | 待验证的持久saver，独立表/命名空间 | 非业务权威 | LangGraph执行器 |

M08落最小消息/run/question账本与幂等，M10增量扩展生命周期和投影，不重建。关系事务只覆盖MySQL内部的业务变更与outbox，不把外部模型/MCP/Redis/Milvus纳入伪分布式事务。事务和行锁不能跨LLM长调用、网络工具或人工等待。

业务更新使用expected_version/CAS或受控行锁；同message重放用唯一键，同operation重放用账本和下游幂等键。Redis锁仅优化，不承载正确性。投影事件包含event_id、aggregate_id、aggregate_version、payload_version；重复幂等，旧版本忽略，缺版本按MySQL最新快照修复。

| 故障点 | 立即行为 | 修复责任 |
|---|---|---|
| 业务提交成功，Redis更新失败 | 返回已持久化事实，可标投影延迟 | outbox重试；缓存缺失从MySQL重建 |
| Milvus仍显示ACTIVE，MySQL已RESOLVED | 拒绝当活动事件合并 | 查询回查归属/状态/version；worker修投影 |
| 工具成功，应用未记成功便崩溃 | operation进入需对账/UNKNOWN，不重复写 | 按operation_id查询下游；已成功补记，明确失败才按策略重试 |
| checkpoint落后于业务账本 | 恢复前查ledger，不盲目重演副作用 | 恢复协调器以账本和工具证据跳过/对账 |
| MySQL不可用 | 不创建case、不批准/执行写工具；不返回已保存 | 返回UPSTREAM_UNAVAILABLE；恢复后按同消息幂等重试 |
| 聚合不确定 | CLARIFY，可不绑定question | 用户补充后新run，保留消息证据 |

不能证明外部exactly-once。只能在定义的下游幂等/查询协议下保证“业务效果不重复”；没有此能力就停止并人工处理。

## 6. 模型与资源边界

[仓库] 云端配置MySQL512MiB、Redis128MiB、etcd256MiB、Milvus5120MiB，总6016MiB/3.75CPU；这不是当前RSS测量。etcd复用原元数据目录、健康后启动Milvus，裁决见ADR021。Redis自身maxmemory64MiB是历史客户端脚本校验项。现有报告只验证小合成数据，不支持容量外推。

[设计] 以下是未来业务run的保守默认，不覆盖M01现有设置：每run总deadline60s；单模型尝试上限20s并受剩余deadline限制；模型并发2；总模型网络尝试最多8（含结构化修复/重试）；检索尝试最多4；工具尝试最多6；ReAct最多6步；全run重试附加次数最多2；工具单次超时10s。只读工具也消耗总预算，副作用不确定不可按通用重试策略重放。数字是可逆工程默认，非原PDF参数。

预算由共享对象原子扣减，禁止SDK、HTTP层、LangGraph节点各自重试相乘。M03真实调用需配置模型标识、任务运行上限与保守计量依据，参数应覆盖必要上下文、输出及诊断复验；这些约束用于防止失控调用。候选池与账号余额的使用遵循AGENTS，不因免费额度用完停止正常验证，也不重建请求预算绕过运行上限。

模型只做抽取、候选决策、归属判断、获准工具选择与润色；权限、状态机、参数、版本、循环边界、输出证据完整性和费用关闸由确定性代码执行。

## 7. 验收入口

能力分期和规模目标只维护在 [ROADMAP](ROADMAP.md)。M00 的设计走查见 [ACCEPTANCE](ACCEPTANCE.md)：三个指定场景使用同一套状态/ID/错误/版本，未定项有后续门禁。运行和协作分别遵守 LOCAL_SETUP 与根 AGENTS，不复制到架构章程。

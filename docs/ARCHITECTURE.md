# DeepHelp 架构章程

版本：`M00-design-v1`；语义契约：`0.1-draft`。这是待复核的工程基线，不是运行中系统描述。来源裁决见 DECISIONS，当前实现状态见 PROJECT_STATE。

## 1. 目标与边界

[源] 复刻意图识别、多轮事件归属、分层记忆、配置化SOP/MCP、调试与评测的核心机制。[设计] 加上可验证的权限、幂等、预算、持久审批和数据发布边界。不是把PDF做RAG问答，也不是复刻内部后台/登录/组织架构。

M08先交付优惠未享受、券不可用、订单活动查询等3–5个合成场景；M14扩展场景，M17做冻结评测，M20迁移物流。真实支付/发券/退款、公司数据、漂亮UI、云端模型训练均不在默认范围。

## 2. 物理目录与依赖方向

[仓库] 根目录已是uv workspace、一个Git仓库与一个根uv.lock。保留现有infra；本轮不创建业务包、不修改锁文件。

```text
modules/deephelp-app/                 # M01创建首个业务workspace成员
  pyproject.toml
  src/deephelp/
    domain/                          # 实体、值对象、错误、少量Port；不导入SDK
    application/                     # intake、13段图、状态转移、SOP用例、outbox编排
    adapters/                        # 模型、MySQL、Redis、Milvus、MCP、HTTP入口
    bootstrap.py                     # 唯一组合根/客户端生命周期
sops/                                # M07起的版本化SOP；不是任意Python脚本
evals/                               # 合成数据、split、指标、冻结manifest
tests/{unit,integration,live,e2e}/    # M01起；tests/docs仅文档工具测试
configs/                             # 可公开模板，不含凭据
scripts/                             # 小型运维/导入/评测命令，不建新平台
planning/                            # 模块计划/子Prompt，按任务读取
handoffs/                            # 已做什么、未做什么和直接下游契约
docs/                                # 架构、语义约束、真实状态
.local/                              # 原PDF、私有配置/上下文输出；永不提交
```

依赖为 `application → domain`，`adapters → domain`，组合根装配两者；HTTP入口可调用application。领域不反向导入FastAPI、LangGraph或数据库SDK。初期单应用进程+一个本地MCP Mock即可，不按规划阶段拆22个部署单元。

只在真实变化点定义Port：ModelGateway（chat/embed可拆窄方法）、IntentRetriever、SOPExecutor、ToolGateway、Case/Message/Operation Repository。简单字段校验和规则函数不套接口工厂。事件裁决的模型适配可复用ModelGateway，不再建第二套模型基础设施。

## 3. 主流程：13段，不等于13次模型调用

| 顺序/标识 | 职责 | 允许的停止路径 |
|---|---|---|
| 100 INPUT_VALIDATE | 参数、长度、请求标识及基础身份校验 | 非法输入统一错误；不创建业务case |
| 200 SAFETY_CHECK | 已验证身份上的权限/内容边界 | 无权限只写安全审计，不为他人建case |
| 300 TEXT_CLEAN | 保留原文，清洗/实体抽取，给话语功能标签 | 无可靠实体保留未知；不可猜测 |
| 400 BUILD_BIZ_CONTEXT | 读取受授权的有界历史/活动问题 | MySQL不可达且需写入时失败关闭 |
| 500 EVENT_CLUSTER | 有界候选、硬约束、补充归属、事实聚合 | 歧义则澄清，不强贴最近问题；M08显式passthrough |
| 600 INTENT_RECOGNIZE | 唯一一次进入主级联决策服务 | 规则/召回/增强/兜底均可拒识；内部尝试另计数 |
| 700 INTENT_CHECK | 注册表、可执行性、决策一致性 | unknown/无路由→澄清或人工 |
| 800 KEYWORD_CHECK | 已确认槽位及冲突校验 | WAITING_SLOT，禁止调用SOP业务工具 |
| 900 FETCH_SOP_ID | 固定SOP版本及工具许可集 | 无SOP/版本无效→人工，不编造流程 |
| 1000 SOP_EXECUTE | 有界ReAct/确定步骤、工具和证据 | 失败/预算耗尽/审批等待；写入前强校验 |
| 1100 REPLY_POLISH | 基于结构化事实生成表述 | 模型失败回退事实模板，不新增业务事实 |
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

一个session可有多个活动question；同订单也可能有多个不同问题，不能用order_id唯一划分case。路由不确定时先保存受授权消息并返回CLARIFY，question_id允许为空；不得把歧义消息写入随机case。等待槽位不使用LangGraph interrupt，不无限挂起执行资源。

普通消息入口与审批恢复入口分开。审批只恢复原run；thread_id由服务端以workflow namespace+run_id映射，tenant作为隔离命名空间的一部分；不能信任客户端提交任意thread_id。审批绑定operation、参数hash、SOP版本、question版本、审批主体和期限。任何变化都使旧批准失效。

## 5. 数据所有权与一致性

| 数据 | 权威 | 派生/缓存 | 写入者 |
|---|---|---|---|
| message、question、实体更正及版本 | MySQL | Redis窗口；Milvus摘要 | intake/业务用例 |
| operation、审批、执行结果证据 | MySQL | 可选只读缓存 | Operation用例/工具执行边界 |
| SOP/Prompt/registry发布版本 | 已发布文件及版本manifest；运行记录固定引用 | 进程缓存 | 指定发布者 |
| outbox事件与投影消费进度 | MySQL | Redis/Milvus目标结果 | 事务内产生，轻量worker投影 |
| 意图语料 | 有来源和split的版本化数据资产 | Milvus索引 | 导入/发布用例 |
| checkpoint | 待验证的持久saver，独立表/命名空间 | 非业务权威 | LangGraph执行器 |

关系事务只覆盖MySQL内部的业务变更与outbox，不把外部模型/MCP/Redis/Milvus纳入伪分布式事务。事务和行锁不能跨LLM长调用、网络工具或人工等待。

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

[仓库] 云端配置MySQL512MiB、Redis128MiB、Milvus5120MiB，总5760MiB；这不是当前RSS测量。Redis自身maxmemory64MiB是历史客户端脚本校验项。现有报告只验证小合成数据，不支持容量外推。

[设计] M00本轮外部模型调用预算为0，不新增依赖/镜像/服务。未来开发保守默认：每run总deadline60s；单模型尝试上限20s并受剩余deadline限制；模型并发2；总模型网络尝试最多8（含结构化修复/重试）；检索尝试最多4；工具尝试最多6；ReAct最多6步；全run重试附加次数最多2；工具单次超时10s。只读工具也消耗总预算，副作用不确定不可按通用重试策略重放。数字是可逆工程默认，非原PDF参数。

预算由共享对象原子扣减；禁止SDK、HTTP层、LangGraph节点各自重试相乘。M03批准live前，必须配置真实模型标识、token/费用硬上限和价格/免费额度依据；未配置或无法保守计费则拒绝live。默认不得因8次额度未用尽而突破费用/时间上限。

模型只做抽取、候选决策、归属判断、获准工具选择与润色；权限、状态机、参数、版本、循环边界、输出证据完整性和费用关闸由确定性代码执行。

## 7. 分期与验收

| 阶段 | 能力 | 明确不宣称 |
|---|---|---|
| M00–M02 | 架构、异步骨架、语义/类型契约、合成fixture | 已有Agent/真实模型效果 |
| M03–M08 | 网关、清洗、Dense、真实MCP Mock、只读SOP、纵向MVP | 完整交错多轮、训练兜底、持久审批 |
| M09–M12 | Hybrid、业务记忆、事件聚合、统一级联 | 训练/持久写入已完成 |
| M13–M16 | FastText、SOP治理、持久审批、输出调试 | 原作者线上收益 |
| M17–M19 | 冻结评测、审核回流、容量和故障验收 | 两周稳定/生产吞吐，除非实际测试 |
| M20–M21 | 合成物流迁移；可选框架对照 | 真正公司部署或模型必然优于另一框架 |

M00通过标准：三个指定走查能用同一套状态/ID/错误/版本解释；两份独立接口草案兼容；未定项有后续门禁；不存在按旧启动包重装/加库指令。细项见 MODULES/M00_ACCEPTANCE.md。

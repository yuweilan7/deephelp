# 跨模块语义契约

版本：`0.1.3-draft`，在M00语义基线上明确分期；M01代码仍使用既有`0.1.2-m01`，本次不修改已实现接口。本文描述当前和未来共用语义，不声明类/表已存在。M02原位发布M03–M08实际需要的类型；后续模块首次消费时增量细化，不在M02一次冻结全部未来协议。破坏已有消费者的变更需版本和影响说明；普通字段细化不必每项新增ADR。

## 哪个阶段实现什么

| 阶段 | 当前要落地 | 只作未来约束 |
|---|---|---|
| M02 | 请求/响应、实体来源、最小Question、IntentDecision、工具/证据结果、当前版本/预算摘要、目录与fixture | DB语义可以设计，不能称已验证数据库；审批恢复/outbox领取不实现 |
| M06–M08 | 工具协议/SOP实际消费字段；MySQL消息/run/question账本、请求幂等 | 核心业务工具只读，Operation/Approval状态不能当可用功能 |
| M10–M12 | 生命周期、缓存/向量投影、事件上下文与增量版本 | 审批/副作用仍禁用，WAITING_APPROVAL仅预留 |
| M15（另选） | 持久审批、操作领取、checkpoint恢复与对账 | 未验证前所有业务写工具继续拒绝 |

所有阶段沿用同一套类型，不另复制DTO。下面关于审批领取、执行重放的细节是M15要求；它们不阻塞只读核心的M16/M17。

## 身份与幂等

| 字段 | 语义与约束 |
|---|---|
| tenant_id / user_id | 可信入口验证或本地测试身份提供器注入；不相信模型/请求正文自报归属 |
| session_id | 会话范围，一个session可含多个question |
| question_id | 一个业务问题；路由未确定前可空；它不是order_id、session_id或thread_id |
| message_id | 客户端/通道稳定消息标识；同(tenant,user,channel,message_id)幂等接收 |
| request_id | 一次传输请求；同message重投可有不同request_id |
| run_id | 一次业务执行；普通新消息通常新run；重复同message应指向已有关联run/结果 |
| trace_id | 诊断链路；不能当授权或幂等凭据 |
| operation_id | 单次业务工具操作的稳定幂等键；重试/恢复沿用，参数变化必须另建并重新审批 |
| approval_id | 一份持久审批记录；绑定operation及计划快照，同一决定重投引用同一记录，不是message_id |
| event_id / version | outbox幂等与业务聚合版本；不等同消息到达时间 |

重复消息payload相同返回既有结果或进行中引用，不新增case/工具调用；相同message_id但正文hash不同返回IDEMPOTENCY_CONFLICT。普通用户补充信息必须使用新message_id。服务端取当前身份及归属，不能靠全局order_id过滤替代租户隔离。

## 最小数据结构语义

RequestEnvelope：schema_version、经验证身份、channel/session/message/request、raw_text、occurred_at、received_at、可选question_hint。hint仅候选，必须授权校验。

Question：question_id、归属、status、version、实体及每项provenance、active_intent、固定registry/SOP版本、创建/更新时刻。实体更正保存新旧值和消息来源，不能last-write-wins抹掉证据。

EventContext：current_message_id、member_message_ids、candidate_question_ids、selected_question_id、cleaned/contextual_text、entities、conflicts、assignment_decision和证据。跨明确订单的相似传递边不自动合并；明确更正走有版本的替换事件。上下文候选为ACTIVE、WAITING_SLOT，WAITING_APPROVAL仅允许授权读取；结束状态默认不参加活动合并。命中后回查MySQL归属、状态和version，不只信向量投影。

IntentDecision：decision=accept/clarify/handoff/reject、final_code可空、candidates、stage_evidence、reason_code、registry_version、policy_version。候选保存原始score和score_kind（cosine/BM25/fusion/classifier_probability等），不能统一叫confidence。非top1选择须含可校验的override_rule_id、理由和证据；任意自然语言解释不算豁免。

ToolRequest/ToolResult：operation_id、run_id、工具名/版本、受控身份、经校验参数及hash、调用预算、结果status、事实、evidence_refs、upstream_request_id、错误分类。数据/日志中不含SDK客户端或秘密。调用ledger是行为证据，模型说“已查询”不是证据。

SOPResult：status、facts、evidence_refs、tool_call_ids、next_action、sop_version、缺失槽位/人工原因。缺槽位由主流程映射Question.WAITING_SLOT和Response.CLARIFY，不混用三类状态。未获证据不能宣称RESOLVED；M15之前所有业务写工具拒绝。

ResponseEnvelope：schema_version、request/run/trace ID、question_id可空、outcome、question_status可空、reply、facts、evidence_refs、next_action、error可空、versions、budget_used。未授权/输入非法可以没有run/question。response不序列化内部提示词、凭据或隐藏推理。

EvidenceRef：来源类型、message/tool/dataset记录ID、版本、片段定位/摘要、内容hash（适用时）；必须能回查合法数据，不指向未授权原始客户数据。

VersionManifest：schema/contract、registry、dataset/split、embedding_signature、model/provider、prompt、SOP、policy、code_commit。值未知就明确null/not_configured，不虚构版本；每次运行固定有效版本快照。

ExecutionBudget：deadline、remaining_attempts、retry_remaining、tool_steps、token/cost_limit、used计数及reason；费用未配置时live禁用。时间/次数/金额任一耗尽即停止，单个策略不能重新创建预算“续命”。

## 状态必须区分

Question：ACTIVE / WAITING_SLOT / WAITING_APPROVAL / RESOLVED / HANDED_OFF / CANCELLED。
Run：RUNNING / WAITING_APPROVAL / SUCCEEDED / FAILED / CANCELLED。SUCCEEDED只表示本轮正确完成，完全可以输出CLARIFY，不等于question已解决。
Operation：PREPARED / IN_FLIGHT / SUCCEEDED / FAILED / UNKNOWN / CANCELLED。UNKNOWN不能被通用重试转为再次写入；先对账。
Approval：PENDING / APPROVED / REJECTED / EXPIRED / REVOKED。批准必须绑定操作参数和版本；恢复执行仍需再次校验授权与状态。

响应outcome：ANSWERED / CLARIFY / PENDING_APPROVAL / HANDOFF / REJECTED / ERROR。问槽位用CLARIFY+WAITING_SLOT，不用HTTP500或审批interrupt；业务拒识/无SOP可以HANDOFF，不等于基础设施故障。

## 错误分类（M02落实HTTP映射）

| error code | 可否自动重试 | 对外/业务效果 |
|---|---|---|
| INVALID_ARGUMENT | 否 | 修正输入；不创建错误case |
| UNAUTHENTICATED / FORBIDDEN | 否 | 拒绝；不泄露对象是否存在 |
| IDEMPOTENCY_CONFLICT | 否 | 同一消息标识不同payload，要求修复调用方 |
| VERSION_CONFLICT | 重新读取后有限尝试 | 不覆盖更新版本，审批可能失效 |
| UNKNOWN_INTENT / MISSING_SLOT / NO_SOP | 否 | 正常澄清/人工next_action，通常不作为传输错误 |
| MODEL_OUTPUT_INVALID | 至多剩余预算内受限修复 | 仍无效则拒识/错误，不接受未知code |
| RATE_LIMITED / TIMEOUT / UPSTREAM_UNAVAILABLE | 只读且预算允许时 | 安全降级/可诊断错误；不能伪报执行成功 |
| BUDGET_EXHAUSTED | 否 | 停止新调用，模板收口/人工 |
| APPROVAL_INVALID / APPROVAL_EXPIRED | 否 | 不能执行原操作；提示重新授权流程 |
| OPERATION_UNKNOWN | 否 | 对账/人工；保留可恢复run及操作引用 |

## 空值、审批领取与重复请求

| 情况 | 语义 / 处理 |
|---|---|
| 未知 | 尚无可靠证据，如question尚未归属或模型版本未配置；用null/not_configured并保留原因，不猜值 |
| 缺失 | 当前步骤必需的业务槽位未提供，返回CLARIFY/WAITING_SLOT；必需的传输字段缺失则INVALID_ARGUMENT |
| 不适用 | 拒绝于入口的请求没有业务run/question，允许null；未调用工具的evidence/tool列表可为空 |
| 非法 | 字段格式、枚举、归属或版本不合法，分别按INVALID_ARGUMENT、FORBIDDEN、VERSION_CONFLICT处理；不能当缺槽位放行 |

上述是语义分类，不新增业务状态枚举。相同消息幂等回放仍返回关联 run/结果；未知主意图和缺槽位不等于非法 HTTP 请求。兼容样例和非 top1 反例见 `ACCEPTANCE.md`；它们是待 M02 落为类型和测试的设计基线，不是已发布 API。

审批记录必须绑定operation_id、参数hash、SOP版本、question_version、主体和期限，并以版本条件更新确保PENDING→APPROVED最多一次。批准后由执行领取事务再次核验绑定与授权，将PREPARED→IN_FLIGHT并标识唯一执行者；批准记录本身不触发第二次工具调用。领取前的实体更正撤销旧审批并取消未发送操作/等待run；已经IN_FLIGHT或UNKNOWN的操作不得伪标CANCELLED，应冻结原参数并对账。

绑定的question_version指领取前的计划快照。领取事务因本次状态推进而产生的新version，要记入执行证据；它不反过来否定已经完成的本次领取。外部更正仍需CAS协调，不能修改已发出的计划。此边界及影响记录于ADR018。

同一审批决定的重投先鉴权，再读取既有决定/执行状态：不再次迁移、不重新领取；已完成返回同operation/run的结果，未完成返回进行中引用或OPERATION_UNKNOWN对账指引。相互冲突的决定或旧版本新决定返回VERSION_CONFLICT/APPROVAL_INVALID；过期返回APPROVAL_EXPIRED，工具0。普通新消息只产生普通消息run，不能借“好的”批准/恢复原run。

## 兼容性与发布

当前特性主写者发布被消费的具体类型和示例；下游导入同一包，不能复制DTO定义。新增可选字段说明默认行为；删字段/改枚举/改变幂等范围必须升级契约并回归直接消费者。全局锁与迁移单独审查，不能两个会话同时修改。

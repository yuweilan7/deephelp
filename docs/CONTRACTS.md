# 跨模块语义契约

当前发布契约：`0.2.0-m02`，唯一类型见 `modules/deephelp-app/src/deephelp_app/domain/models.py`。从 M01 的 `0.1.2-m01` 升级：facts/evidence 由自由 dict/string 改为 Fact/EvidenceRef，next_action 改为枚举，新增 run_status、tool_call_ids、missing_slots；旧版本 envelope 拒收。现有 API、预算、fake 和离线消费者按新类型回归；converse 的业务执行边界见 PROJECT_STATE。

本文同时约束未来模块，不声明数据库表或审批能力已存在。后续首次消费时增量细化，不在 M02 冻结全部未来协议。破坏已有消费者的变更需版本和影响说明；普通字段细化不必每项新增 ADR。

## 哪个阶段实现什么

M05在唯一domain/models.py增量发布CorpusRecord、DenseScope、DenseHit、DenseCandidate、DenseResult，ports.py发布DenseRetrieverPort；envelope仍0.2.0-m02，旧消费者兼容。CorpusRecord只接受actionable目录标签及合成语料。DenseScope绑定namespace/dataset_version/registry_version/完整EmbeddingSignature；同维度不同模型不等价，scope改变使用另一个集合。namespace为语料范围，不是用户授权凭据。

DenseRetrieverPort.retrieve(text, scope, budget, top_k=3)复用原ExecutionBudget，返回DenseResult。hits含doc_id/intent_code/content/raw_score/score_kind=cosine/metadata/scope；candidates按per_intent_max_v1取每类最佳样本，保存连续rank及evidence_doc_id，policy_version=dense-baseline-v1。cosine不是confidence或概率，候选不等于最终IntentDecision；只有后续600主意图服务决定接管。manifest及本机指针不是业务事实账本。

M04增量类型仍在唯一domain/models.py，业务envelope保持0.2.0-m02，无破坏性接口变化：

- TextCleanResult含raw_text、cleaned_text、连续完整segments及width-whitespace-v1；不截断原文。分段start/end是cleaned_text字符索引，段拼接必须还原完整cleaned_text。
- ExtractedEntity继承Entity；start/end是raw_text的Python字符索引，source.excerpt必须等于该切片，value等于该片段的全半角ASCII规范化。layer=regex/api/strong；confidence_kind=deterministic/model_grounded，不是校准概率。disposition保留observed/negated/correction。同消息更正保留所有observations；跨消息更正复用EntityConflict及旧/新来源。
- TextEntityResult含唯一槽位entities、全部observations、conflicts、unresolved_fields、demand_type、rule_matches、layers与提取/提示版本。已确认旧值可为保留历史而继续出现在entities；只要槽位列于unresolved_fields，下游就必须澄清，不能据该值执行工具。模型不能覆盖confirmed；confirmed必须来自已授权的前置消息，归属回查属于M11/主流程。
- model_coverage_complete为null表示未用API，false表示所需字段没有得到完整且有效的原文分段覆盖（包括分段上限或后段失败）。这类模型观察保留但不确认新槽位；完整Regex实体仍可保留。layers分列调用数、接受/拒绝观察数、耗时、错误/request ID及API原文段范围。
- RuleMatch只含已登记叶子的candidate_code、条件/排除项对应的rule_id/version、优先级及原文证据。规则表complaint-rules-v1在text_entity.py；300不产生IntentDecision/final_code、不路由SOP，600统一消费候选。demand_type=main/supplement/new_topic/unknown只作话语功能标签，不等于主意图或已完成问题归属。

M03 增量添加 ChatRequest/ChatResult、ChatMessage/ModelTool/ModelToolCall、ModelUsage、EmbeddingSignature/EmbeddingResult，仍在唯一 domain/models.py；ChatPort/EmbeddingPort 在 ports.py。旧 ModelGateway.complete 保留兼容，业务 envelope 版本仍为 0.2.0-m02。预算增加可空 token_upper_bound/cost_upper_bound 与默认 0 的 uncertain_attempts，不改已有状态/身份语义；新增 PROVIDER_QUOTA_EXHAUSTED / MODEL_CAPABILITY_UNAVAILABLE 非重试错误。费用 cost 是配置单价下的保守估计，未知尝试只留上界，真实付款不由 DTO 推断。

调用者必须共享原 ExecutionBudget，model 调用前有正 token/cost 上限；超时/取消不释放可能已发生的计费预留。ChatResult 保存 usage/finish_reason/provider_request_id 与校验过的 schema/tool 字段，EmbeddingResult 按输入顺序含签名、缓存命中和索引适配标记。模型结果不是业务事实，模型工具调用不是 ToolResult 或 MCP 执行证据；下游仍做白名单、身份/归属和参数核验。接口与版本限制见 [模型矩阵](MODEL_CAPABILITIES.md)。

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

### 当前类型的具体边界

- `ConverseInput` 只含不可信消息字段；`RequestEnvelope.identity` 由可信入口注入。DTO 结构不替代鉴权，`domain/checks.py` 检查完整 tenant/user、session/question 归属及工具上下文；真实存储回查由后续消费者负责。
- 目录版本 `complaints-v1`，三类 actionable code 为 DISCOUNT_MISSING / COUPON_UNUSABLE / ORDER_ACTIVITY_QUERY；另有三个不可执行父节点，l1–l4 与 parent 显式校验。规则/分类候选只能选择叶子，不接受未知 code。
- 候选 `rank` 显式从 1 连续排列，原始 `score_kind` 保留；不能跨不同打分种类直接比较大小。当前非 top1 策略仅 `prefer_complete_slots_v1`：top1 缺槽、选择项槽位齐全，并有可与实际实体消息核对的证据；任意解释或未知策略 ID 拒收。该策略不替代 M12 分类与阈值校准。
- ID 与实体值是严格字符串；实体必须有 message_id/excerpt。Question 保存当前每槽唯一值、旧/新 EntityConflict 和成员消息，明确更正不可丢旧来源。金额为非负 Decimal，输入只收 Decimal/十进制字符串，CNY 最多两位小数，不静默舍入。
- Fact 声明 text/money/flag 并引用证据 ID。ToolResult 成功需要 call_id 与同一调用的 tool evidence；SOP/Response 中工具调用 ID 与 tool evidence 必须一致，RESOLVED/ANSWERED 需要有证据的事实。这些是本地结构约束，真实调用 ledger 与合法证据回查由 M06–M08 保证。
- `response_from_sop` 只做状态映射：WAITING_SLOT → CLARIFY / WAITING_SLOT / run SUCCEEDED / 工具 0；不是 SOP 执行器。缺槽位不能接收成功事实或调用。当前只读工具白名单为 get_order_benefits、check_coupon，参数 hash、当前实体/归属/意图及预算均须检查。
- `ExecutionBudget.snapshot()` 复用 M01 同一运行时预算，将 deadline 表达为当时剩余秒数；序列化摘要不含单调时钟或运行时对象，不能拿摘要重建/延长原预算。token/cost/tool 上限未知保持 null；真实运行时额度计量属于 M03/M07。
- WAITING_APPROVAL/PENDING_APPROVAL 枚举仅保留名称，当前 Question/Response 拒收；没有批准/resume 输入或写工具。审批执行与数据库唯一约束没有因此被实现。
- 合成目录/样本/指标定义见应用 `sample_data/README.md`，固定回归数据不得冒充 M17 未见评测集。

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

上述是语义分类，不新增业务状态枚举。相同消息幂等回放仍返回关联 run/结果；未知主意图和缺槽位不等于非法 HTTP 请求。HTTP 默认映射见 `errors.HTTP_STATUS`：身份 401/403、幂等/版本冲突 409、输入 422、限流 429、超时 504；UNKNOWN_INTENT/MISSING_SLOT/NO_SOP 是 HTTP 200 的正常人工/澄清收口。总 deadline 可明确映射 504。审批/操作未知错误的映射是未来边界，不表示审批可用。

FakeRepository 的内存用例按 tenant/user/channel/message 作用域保留首次结果；同逻辑消息换 request/trace/received_at 不替换 run，正文/session/occurred_at/question_hint/schema 变化返回 IDEMPOTENCY_CONFLICT。它没有事务、持久化或进程崩溃保证，M08 仍须验证 MySQL 唯一约束。兼容及后续业务验收责任见 `ACCEPTANCE.md`。

审批记录必须绑定operation_id、参数hash、SOP版本、question_version、主体和期限，并以版本条件更新确保PENDING→APPROVED最多一次。批准后由执行领取事务再次核验绑定与授权，将PREPARED→IN_FLIGHT并标识唯一执行者；批准记录本身不触发第二次工具调用。领取前的实体更正撤销旧审批并取消未发送操作/等待run；已经IN_FLIGHT或UNKNOWN的操作不得伪标CANCELLED，应冻结原参数并对账。

绑定的question_version指领取前的计划快照。领取事务因本次状态推进而产生的新version，要记入执行证据；它不反过来否定已经完成的本次领取。外部更正仍需CAS协调，不能修改已发出的计划。此边界及影响记录于ADR018。

同一审批决定的重投先鉴权，再读取既有决定/执行状态：不再次迁移、不重新领取；已完成返回同operation/run的结果，未完成返回进行中引用或OPERATION_UNKNOWN对账指引。相互冲突的决定或旧版本新决定返回VERSION_CONFLICT/APPROVAL_INVALID；过期返回APPROVAL_EXPIRED，工具0。普通新消息只产生普通消息run，不能借“好的”批准/恢复原run。

## 兼容性与发布

M06复用上述ToolRequest/ToolResult，新增内部ToolInvocationEnvelope（请求及request/trace/call ID）和ToolInvocationRecord（主体、实际参数、状态、错误码、证据ID）。增加NOT_FOUND/404，不自动重试；既有类型和字段默认行为保持兼容。两个工具仍为get_order_benefits与check_coupon，模型可见参数只有order_id和对应必需coupon_id。

ToolPort.execute(request, question, budget, context=已验证RequestEnvelope)返回ToolResult。入口必须提供可信context；网关校验它与request/question的主体、session、槽位、版本及可执行状态。stdio不提供登录认证：本特性的独立合成调用者只证明协议和归属检查，应用认证接入由M08负责。

受控stdio子进程通过环境接收随机会话密钥；每次调用的内部_meta携带HMAC-SHA256签名信封，绑定主体、参数、版本及唯一call ID。服务端验证签名、拒绝重放并再次核对订单/券归属；正文身份和故障开关不在schema中。密钥/签名不返回模型或写入ledger。ledger/health为签名保护的管理resource，不注册为工具。

单次调用预留共享ExecutionBudget后发送，不自动重试；内部相对预算含当前已预留的一次调用。超时返回TIMEOUT，主动取消保持CancelledError；实际已发送的失败保留客户端call ID供ledger核对。成功事实需匹配操作、工具版本、对象、类型和证据hash；缺字段/矛盾/超大返回体以MODEL_OUTPUT_INVALID收口，无成功事实。工具返回中的文字仅作为数据。ledger的succeeded表示合成查询完成，网关对损坏响应的拒绝另记diagnostics；ledger不是副作用账本或幂等保证。

当前特性主写者发布被消费的具体类型和示例；下游导入同一包，不能复制DTO定义。新增可选字段说明默认行为；删字段/改枚举/改变幂等范围必须升级契约并回归直接消费者。全局锁与迁移单独审查，不能两个会话同时修改。

M07发布 `SOPExecutorPort.execute(question, budget, context=可信RequestEnvelope, run_id=...) → SOPResult`。Question承载已确定意图、唯一带来源槽位、归属和固定SOP版本；执行器先检查主体/session/状态/版本，缺失或冲突槽位返回WAITING_SLOT，模型/工具0。它不重新分类或修改Question；已有response_from_sop负责状态映射。独立受控执行单元未依赖LangGraph，未来执行框架只替换这个端口。

ToolRequest新增可选 `timeout_seconds=null`，null沿用网关子时限，非空只能收紧；身份/预算/timeout均不在模型参数schema中。ToolPort.execute增加可选运行时 `on_dispatch(call_id)` 回调，默认null兼容原消费者，网关在发送await前调用；它不属于DTO，用于总deadline期间保留已发送尝试。M06的事实/证据检查提取为共享validate_observation，网关与执行器使用同一规则。

SOPResult与ResponseEnvelope的tool_call_ids语义细化为所有已发送尝试，包含失败/被取消的调用，ID不重复。所有工具证据必须指向其中的成功调用；失败ID自身不是成功证据，RESOLVED仍必须有事实/证据。旧有全成功结果兼容；缺槽位仍禁止工具ID。入口FORBIDDEN仍是无事实/调用的REJECTED；已鉴权运行中查询被下游拒绝可为ERROR/FAILED并保留实际调用ID，不能发布对象事实。业务envelope仍0.2.0-m02，现有消费者完整回归；此边界不实现持久审计/审批账本。

M08复用`0.2.0-m02`，ResponseEnvelope新增可选`stages=()`、`intent_decision=null`、`disabled_features=()`、`replayed=false`。StageReport记录stage/status/elapsed_ms/model_calls/tool_calls/error_code；模型尝试计入300/600/1000，实际工具尝试计入1000。score_kind新增`model_choice`，score=1只表示schema分类选中项，不是校准概率；Dense原始余弦保留为候选及数据证据，不跨打分种类比较。

`Conversation.run(request,budget)`处理当前完整消息，只有600进入IntentService。`MessageLedger.accept`返回新领取或原run/Question/结果，`finish`以原Question version+1及RUNNING条件更新run/Question；同消息payload内容范围沿用既有契约，传输ID不进入hash。MySQL三表的唯一接收与终结事务已由M08消费；相同消息回放使用本次request/trace ID、原run/question与事实结果、当前预算0。RUNNING仅返回进行中引用，不抢占或恢复；不承诺硬中断后的自动恢复。

M08缺槽位提示新消息完整重发，跨消息entities/事件/记忆未启用；question_hint先按主体/session回查，再以INVALID_ARGUMENT拒绝恢复，未授权hint为FORBIDDEN。取消终态保留已发送调用ID，但无成功事实；提交失败不返回ANSWERED。唯一事实模板只消费经M06/M07核验的Fact与配置结论。本机Bearer令牌映射可信合成身份，正文无身份/批准字段；不等于企业登录系统。M01未组装业务端口时的离线501行为保持兼容。

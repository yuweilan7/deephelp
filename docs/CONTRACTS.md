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

M09保留DenseScope原字段/指纹/集合名，新增其子类HybridScope：必须显式`index_kind=intent_hybrid`，`analyzer_version=jieba-search-cn-lower-v1`，独立`m09_intent_`集合。历史/事件集合不能进入此端口；尚无用户历史召回，不以意图scope替代用户namespace/state权限过滤。HybridRetrieverPort继承DenseRetrieverPort，新增同query `compare`，仍返回同一DenseResult/Hit/Candidate；无第二套领域类型。

Hit/Candidate的score_kind增量支持cosine/bm25/fusion：COSINE有界，BM25非负无界，fusion由归一化WeightedRanker返回。Hit保留raw_dense/raw_sparse/fusion_score/rank/matched_sources；null表示该文档不在该路线的前K候选中，不表示真实相似度为零。每路ANN和最终返回均K，融合联合候选最多2K，不能声称计算量相同。按意图取实际返回样本的最大分数，不按样本数量求和。DenseResult保留旧默认，增加mode、候选预算、权重和本次三路总检索耗时；单路SDK耗时在A/B报告中另列。所有分数不是最终正确概率。

IntentDecision增加可选`retrieval=null`，保存600实际候选/分数/版本；分类结果仍由唯一IntentService给出，原envelope版本和消费者兼容。M09发布指针一次原子替换已验证scope、manifest、完整冻结策略与上一版本；启动核对index/dev/test摘要、权重及候选预算。旧M05指针仍装配原Dense端口，M09指针装配Hybrid，不自动改默认配置。checkpoint/审批/写工具边界不变。

学习环境网关扩展：ChatRequest默认输出2048 token，移除4096输出、32消息与16工具声明的本地上限；新增可选enable_thinking=null（继承ProviderConfig）和thinking_budget=null（继承配置）。ProviderConfig默认不开推理、思考额度8192；思考启用后在原预算预留中另计思考额度。请求/响应默认1MiB/16MiB，可配置更大；max_texts默认128，可配置更大。模型自身能力/错误、任务deadline/累计预算与结构/工具参数验证仍由原网关处理。既有业务调用显式输出参数不改变，消费者继续导入同一DTO。

## M10 多问题记忆契约

`ConverseInput/RequestEnvelope`兼容新增`expected_question_version`：提供时必须有`question_hint`，参与幂等摘要；未提供的旧消息摘要保持不变。无hint新建问题；hint只续接同tenant/user/session的ACTIVE或WAITING_SLOT，先检查版本、未完成run和消息时间。接收续接与终态各增一次question.version。重复消息先回放原run结果，重开后重投旧消息也不再执行工具。普通新消息不恢复RUNNING或审批。

Question新增`unresolved_fields`，保留已确认实体并阻止冲突字段进入工具；`conflicts`保留更正链及消息来源。300复用M04合并，600保留显式问题的已绑定意图，话题不符需新建；自动归属留M11。关闭由SOP事实/流程结果或有证据的用户确认决定，TTL不改变业务状态。

`CaseRepository`读取有界snapshot、归属内question和CAS transition；`MemoryPort.load`返回`MemoryWindow`，包括有channel/message/question引用的历史、开放Question和已结`CaseSummary`。MySQL始终核验事实；向量候选必须同时匹配主体/session、当前status和version，过期ACTIVE不能恢复问题，摘要不写回实体。

`GET /memory?session_id=...`读取当前身份的窗口。`POST /questions/{question_id}/state`接收`LifecycleCommand(session_id,expected_version,target,reason,evidence_source,evidence_ref)`；同主体/session校验、版本冲突409，禁止修改RUNNING问题。RESOLVED要求user_confirmation/process_result，CANCELLED要求operator_cancel，HANDED_OFF要求operator_handoff，开放状态切换要求slot_check；关闭后只允许explicit_reopen→ACTIVE。WAITING_APPROVAL仍禁用至M15。这些是问题状态操作，未接入业务写工具。
## M11 事件归属契约

复用唯一领域DTO，新增`EventMessage/EventCandidate/ClusterJudgement/EventContext/ClusterEdge/EventClusterResult`和`ClusterJudgePort`。`EventAggregationService.aggregate(request,budget)`返回聚合结果，不拥有主意图或SOP。窗口来自同主体/session MySQL事实；开放问题含WAITING_SLOT，已结及待澄清事件不参与自动归属；WAITING_APPROVAL仍由既有M15禁用校验拒绝，不恢复审批。

`ClusterJudgePort.judge(current,candidates,messages,budget)`至多调用一次，返回attach/new/uncertain、候选ID、把握等级、关系和原文引用。只有候选中的目标、高把握及当前/目标两侧精确引用可attach；代码检查scope/status/version、实体冲突、簇级订单和独立问题约束。cosine是候选证据，不是概率；低相似门禁只作保守排除，不据小样本宣称校准接管阈值。明确实体更正是成员更新，图中不作订单等价union。

`PersistentEventAggregation.process`通过M10接受消息、CAS终结和事务outbox保存归属。账本新增`lookup(request)`及可选可信`attribution=(question_id,expected_version)`；存储/幂等hash仍取原始请求，不能把内部选择的hint写成用户payload。scope/开放状态/版本/乱序/未完成run在事务内再验。重复终态原样回放事件图、模型/工具0；未知RUNNING拒绝重领。`ResponseEnvelope.event_cluster`和`Question.event_summary/aggregation_pending`均为默认兼容字段，无新表或依赖。

摘要为有出处的原文摘录，保留实体冲突和更正来源，不能成为新业务事实。多个主诉结果的`current_event_context=null`且要求分条，数据库只保存待澄清消息，不宣称已建多个事实问题。待澄清问题可用明确hint补充；未归属补充不能污染既有问题。独立CLI与显式无持久化preview见应用README，完整500→600接入由M12完成。

## M12 完整主链与级联契约

明确补充订单/券号且只有一个相容开放事件、确实补其缺失槽位时，500按已确认绑定确定性关联，仍检查整簇和接收CAS；不调用归属模型。多个相容事件、更正或一般语义归属继续使用受控裁决。此守卫只消费既有事实，不进行主分类。

同一`Conversation.run`装配`EventAggregationService`后，300提取当前原文一次，400读取MySQL事实窗口，500复用两者归属，再以原始请求payload及可信attribution原子接收。预接收的阶段trace缓冲到真实run/question确定后发布，不产生伪业务ID。已有终态先lookup直接回放；并发接收仍由原账本唯一键/CAS决定，不抢占RUNNING。未授权hint在写业务数据前拒绝。

500只消费已绑定`active_intent`的槽位相容性，不调用分类器：自动券号补充排除不需要券号的事件。`EventCandidate.active_intent=null`为兼容字段。多诉求/未知补充单独保存为待澄清事件，600及SOP跳过；无实际缺槽位的归属澄清保持ACTIVE，实际缺/冲突槽位才WAITING_SLOT，避免伪造missing_slots。独立M11仍保持原入口语义。

600唯一进入`IntentService`，其内部规则、当前检索、必要记忆增强检索、FallbackPort顺序执行。已绑定事件保持意图；未绑定事件的增强查询只来自当前已授权聚合及确认实体，不混入其他问题或闭合历史。`FallbackPort.decide(text,retrievals,budget,context=...)`返回`FallbackResult(code,reason)`；supported与非空注册叶子双向一致，unknown/multiple必须code=null。schema不合法或虚构代码安全转人工；真实服务失败停止下探。FastText默认disabled，M13显式指针可启用适配器。

正式live的FallbackPort使用已验证的强模型端点，与500归属共用`event-judge.example.json`；VersionManifest.model/prompt记录主分类兜底模型与`m12-fallback-v2`。Embedding/提取/SOP继续使用原provider配置，其调用仍在各自阶段计量。

`IntentDecision.policy_version=cascade-policy-v1`、`cascade_steps=()`为增量字段，步骤保留层次/action/reason及实际检索候选/原始分数、检索与模型尝试。直接接管保存实际有序候选且只选top1；Fallback选择为类别决策，其1不是概率，原召回排名仍在步骤中。当前/增强fusion分别校准，cosine/BM25未校准禁直接接管。配置绑定scope、语料摘要和融合权重；不匹配须重新校准，旧M05指针显式兼容无分数接管。

`StageReport.intent_calls/retrieval_calls`及trace同名字段默认0。`ResponseEnvelope.call_counts`给出本请求的intent/model/retrieval/tool实际计数；正常业务主分类1，归属澄清/预处理故障/重放0。重放的stages仍是原执行证据，本次call_counts与budget_used重新计算，不能把历史阶段计数当新调用。模型计数含Embedding/归属/提取/SOP，检索计数为600检索Port尝试，工具计数为实际dispatch ID；不把这些次数合并为一个“分类次数”。

节点串行更新唯一Question/entities，无并行reducer覆盖。合并保留全部来源及更正链，unresolved_fields始终阻断工具；900核验SOP版本，1000不再分类，1100只用经验证事实。短路都经过1200/1300；终态写最多5秒并在返回前成功提交，失败不发布ANSWERED。取消传播并保留调用ID，无成功事实。DTO没有客户端、锁或向量数组；checkpoint不承担账本职责。

并发请求可能在原子接收前分别计算有界归属，只有唯一接收者进入600/SOP；落选请求的call_counts保留实际预处理用量。已有终态的正常重放先lookup，主分类/模型/工具均0。跨进程预处理不是分布式执行锁，副作用能力仍需M15。

## M13 FastText 可选兜底契约

`FastTextFallback`包装既有`FallbackPort`，仅在600规则/检索未接管后使用；默认构造仍为`StructuredFallback`。`FallbackResult.source`默认为structured，`fasttext`默认为null，旧Port及保存的结果保持可读。source=fasttext必须有同code且need_review=false的合法预测；其他来源可携带拒识诊断，不能把它解释成FastText最终选择。

唯一DTO的`FastTextPrediction`保存五类显式label→code映射、top_k/rank/raw_probability、final_intent_code、confidence_kind=classifier_probability、is_actionable/need_review/unknown、理由、模型/预处理/门限版本及量化标记。unknown/multiple标签的code为null；已选code只能等于top1且理由为calibrated_top1。概率是绑定原始softmax值，包含1e-5数值偏移，不是正确概率；兼容IntentCandidate的score最多1，完整原值仍在CascadeStep.fasttext，不作跨来源概率比较。

训练与推理共用全半角/空白规范化、Jieba0.42.1/HMM关闭、基础词典与业务词典hash；签名变化、绑定/模型hash/标签不匹配或模型损坏使显式启动失败。空输入、仅编号、OOV、低分/小间隔、未知/多诉求及非法预测不会接管，沿同一预算进入既有结构化兜底。FastText可先给出意图，600再排除未解析槽位决定CLARIFY；缺/冲突槽位仍禁工具。

本地CPU推理单独保存elapsed_ms，不伪增模型API次数/token/费用；有界并发2、输入≤2000字符并共享deadline，取消等待该短CPU任务退出后传播。模型API保留原累计预算与有限重试。VersionManifest新增三个默认null字段fasttext_model/fasttext_preprocessing/fasttext_policy，已接收问题/重放保留原版本事实；指针变更在新装配时生效，不能改写旧run。

manifest记录三split摘要、实际seed/config、门限、模型bytes/hash和预处理签名。原版/量化分别只用dev校准，test不选参。activate先hash/标签/预处理/内容回读再原子替换active/previous，重复发布同版本不覆盖previous；失败保持旧指针，rollback同样先验证。该指针只选FastText，不替换M09语料或Embedding签名。

## M14 SOP治理契约

`SOPExecutorPort`签名保持。`SOPRegistry`固定三个已注册叶子的映射、独立SOP版本、Prompt版本/全文和工具schema签名；canonical内容摘要为不可变快照ID。`GovernedSOP`使用lookup/branch/end有向无环图，条件仅eq/empty/nonempty/fact_eq/money_lt/money_gte；引用为`lookup节点.事实名`，按所有前置路径检查可用性及类型。当前拒绝循环，不执行任意配置代码。每lookup的失败边只能到FAILED/HANDED_OFF终点，RESOLVED/NEEDS_APPROVAL必须有成功预检证据。

授权上限由代码固定：优惠/活动仅get_order_benefits；券问题可check_coupon及get_order_benefits。配置只能缩小范围，不能增加主意图或工具。模型只看当前就绪工具，动作/参数再次核对已确认槽位；主体/session/对象归属、schema/版本/hash、尝试ID、预算/共享有限重试及终止仍由原Gateway与执行器核验。券未达门槛分支按合成fixture口径比较实付与门槛，不推断企业结算计算规则；可用券只确认查询状态。

`VersionManifest`新增默认null的`sop_registry/sop_snapshot/sop_prompt/tool_schema`，旧DTO/保存结果兼容。主链绑定新问题时固定这些字段；补槽按已有快照查历史版本，缺失/冲突失败关闭，不换成active重执行。应用装配时加载active及保留历史，发布不改变运行中的对象；本机写锁与原子指针只证明单机发布，保留最多64快照，达到上限明确停止，不自动淘汰旧问题版本。原M07旧问题仍按包内旧定义执行。

`SOPResult.node_path`与`ResponseEnvelope.sop_node_path`记录实际配置节点。失败尝试仍保留tool_call_ids；部分来源失败不发布成功事实，最终结论由代码分支和成功证据产生。没有额外主分类或调度服务。

`SOPStatus.NEEDS_APPROVAL`配`NextAction.REQUEST_APPROVAL`及唯一DTO `SOPPlan`。计划绑定operation_id、模拟动作、主体、问题ID/执行输入版本、只读预检参数及hash、SOP版本/完整快照和证据IDs；当前唯一模拟动作simulate_discount_adjustment没有可调用写工具。`response_from_sop`将其映射为HANDOFF/HANDED_OFF/run SUCCEEDED和`sop_plan`，明确未申请、批准或执行。PENDING_APPROVAL与WAITING_APPROVAL仍被DTO拒绝，M15前没有持久批准恢复。M15必须重新核对当时的最新问题版本、归属/状态/期限/参数与操作领取，不能直接把此计划当授权或复用普通消息批准。

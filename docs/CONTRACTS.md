# 跨模块语义契约

当前发布契约：`0.2.0-m02`，唯一类型见 `modules/deephelp-app/src/deephelp_app/domain/models/__init__.py`。从 M01 的 `0.1.2-m01` 升级：facts/evidence 由自由 dict/string 改为 Fact/EvidenceRef，next_action 改为枚举，新增 run_status、tool_call_ids、missing_slots；旧版本 envelope 拒收。现有 API、预算、fake 和离线消费者按新类型回归；converse 的业务执行边界见 PROJECT_STATE。

本文约束接口语义，不充当实现状态或运行说明。旧阶段的MySQL/自建saver名称保留其历史契约背景，当前后端以M25/M26装配和ARCHITECTURE为准；已交付/未实现项只查PROJECT_STATE，具体源码路径见学习导航。新增消费者时增量细化，破坏已有消费者的变更需版本和影响说明；普通字段细化不必每项新增 ADR。

HTTP/工具DTO和wire保持同一版本；Port在domain，业务用例在application，外部实现/发布读侧在adapters，装配CLI在bootstrap。学习、demo、冻结评测与发布prepare/validate只由deephelp-tools消费，运行包无反向依赖。MCP显式选择stdio模块或独立HTTP服务，Hybrid提供已发布语料；不隐式启用demo或回退数据库。职责与路径见[源码导航](learning/source-navigation.md)，原拆包证据见[M19交接](../handoffs/M19.md)。

## M25 PostgreSQL装配

新源码所有关系事实由同一PostgreSQL应用库承载；连接必须显式 `DEEPHELP_POSTGRES_CONFIG`，不回退MySQL/demo。唯一DTO、HTTP/MCP wire保持；新工具证据来源为`business-postgres-v1`，历史`business-mysql-v1`结果/清单/签名字节原样读取，不重新执行。`PostgresLedger/PostgresCaseRepository/PostgresHistory`保留接收/终态/身份契约；原MySQL仅由tools的专门只读快照入口访问，旧数据/安装保留给历史与恢复消费者。

`m25_legacy`保存typed checkpoint、parent/pending writes与迁移进度，tools的`LegacyCheckpointArchive`只读。M26正式PG审批只注入显式打开的官方`AsyncPostgresSaver`薄接缝，独立schema为`deephelp_checkpoints`；业务连接与保存器使用同一应用配置、独立连接池。app migrate执行官方setup，serve不隐式迁移。学习查询读取正式saver和原图，不另建审批流程。业务写效果仍由操作账本与下游对账证明。

旧状态通过官方公共aput/aput_writes转换，保留原ID/parent/metadata/频道与任务版本及pending writes索引/路径；整数线程继续锁定的公共整数版本序列，新线程使用官方序列。原绑定缺失/失效不能因转换补写或重签。已终态的完整发布操作可依原账本修补checkpoint而不加载替代运行或重发效果；未终态操作仍须原完整运行/资产门禁。旧schema不适用的release返回明确配置错误，不补字段后冒充原签名。

短写事务使用Read Committed+唯一约束/显式锁/CAS；记忆多表窗口和业务事实+不可变证据使用局部Repeatable Read，避免拼接不同时间的版本；导出/全表校验为独立read-only一致快照。JSON保留原文本、bytea原bytes、金额numeric(20,2)/Decimal、UTC微秒和前导零文本。Windows PG池独立持有Selector线程，调用入口保留Proactor子进程能力；不改全局policy。

## M20业务数据与网络MCP

Business服务独立包为deephelp-services，app不导入services；唯一输入/记录DTO仍在domain/models.py，Money/VerifiedIdentity/ToolRequest/ToolResult不复制。服务拥有`dh_m20_orders/coupons/activities/order_activities/evidence`事务，消息/问题/操作账本、outbox由原应用直接维护，checkpoint历史在M25仅只读。业务来源由显式stdio或`--business-mcp-url`互斥选择，数据库失败关闭，无demo回退。

应用身份入口的维护API为`POST/GET /business/{orders,coupons,activities}`、`GET/PUT /business/{kind}/{id}`、`GET/PUT /business/orders/{id}/activities`及`GET /business/evidence/{evidence_id}`。服务内部同一资源在`/records`下；请求需服务Bearer及签名委托的完整tenant/user、method、target、body_hash、nonce、audience=business和60秒expires_at。正文没有归属字段；过期/篡改/重放拒绝。返回记录包含identity、正整数version、typed data；调用端回验身份和结构。分页offset/limit，limit 1–100，返回items/next_offset；PUT完整data加expected_version，ID与路径须一致，旧版409。创建同主体/ID/内容重复返回原记录，不同内容409；无删除，停用使用status。活动关系替换与订单版本递增在同一短事务内，关联订单/券/活动必须同主体。

金额只收十进制字符串/Decimal，CNY最多两位，业务最大值受DECIMAL(20,2)约束；回读以SQL精确金额列为准。券有效期、当前订单状态/实付与显式券状态共同决定usable；活动查询只返回当前有效且启用的订单关联。规则/有效期等完整资料由维护详情读取，既有工具事实schema保留。

网络工具仍为两项m06-readonly-v1 wire/原inputSchema/outputSchema；版本表示已有工具契约，不表示使用demo数据。身份和关联ID在受保护元数据，工具参数只含订单/券。正式MCP2.2.0的实际HTTP initialize/list/call、structuredContent、schema/语义和取消各有独立验证；stdio签名使用父子进程临时key，网络另用持久Bearer+委托key，两者不混用。服务限制Host/Origin且目前仅loopback；MCP session/trace不等于业务身份。网络客户端每个有界请求初始化SDK会话，重启后新调用重新连接，未知旧调用不自动重放。

数据库工具证据version为business-postgres-v1，demo仍为business-fixtures-v1。显式装配向Gateway/SOP注入证据来源，ToolResult实际call_id、record_id、事实hash与双端语义守卫保持。查询将订单/券/活动完整版本和当时结果保存为不可变、主体隔离的evidence快照，GET可回查。新message_id读取新提交数据，旧message_id沿用原receipt/事实/证据及本次零预算；不改旧回复。

维护是用户手工资料操作，模型目录没有维护命令；synthetic_rights仍为合成效果，并未接入本次业务表。完整发布仍须精确绑定/内容验收，未产生新发布或远端部署。

### M20独立检索MCP

Retrieval独立进程复用原DenseRetriever/HybridRetriever、Embedding网关及Milvus适配器；应用以RemoteRetriever满足原Port，业务引擎不复制。服务只拥有显式发布scope、语料reference/train记录、Embedding签名、K、weight与冻结策略，不导入/重建索引。profile_id绑定这些资产及现行Embedding请求配置。私有事件记忆与事实校验仍在应用，事务、outbox及checkpoint不搬到检索服务。

唯一DTO为RetrievalQuery/Context/Allowance/Delegation/Action/Reply/Wire。两个只读工具`retrieve_intents`及`compare_intents`输入只含query（非空、≤2000字符）、top_k（1–20）和scope_id（完整指纹），拒绝额外字段与任意集合/路径/连接。普通检索返回发布模式；Hybrid对照返回同查询Dense/BM25/Hybrid三组DenseResult，保留policy_version、candidate_budget、weight、候选ID/排序、score_kind、原始分数及融合来源。cosine、BM25与融合分数不是概率。纯Dense发布不提供BM25对照，返回VERSION_CONFLICT。

使用正式Streamable HTTP及实际initialize 2025-11-25；Bearer和委托key与Business分开，audience=retrieval。短期签名绑定允许的tenant/user、关联ID、工具名、query_hash、profile_id和有限Allowance；正文/session/trace不授权。服务把允许主体映射到自身固定发布scope，查询scope须完全相同；启动健康资源和双端语义校验核对语料、签名、版本、ID/内容、每意图最大证据、融合公式和来源。

原ExecutionBudget不跨网络。应用在派发前原子预留本次全部尝试、Embedding输入上界及费用上界并持久化；服务重建零重试、有截止时间的子预算，复用原网关/数据库RPC计数。服务回执记录真实MCP、Embedding、Milvus验证、Dense、BM25及fusion动作、延迟、provider_request_ids、实际usage与未知预留；签名绑定整个Reply。调用端回验签名、关联上下文、schema、候选语义与有限用量，再结算原累计预算。未知/断连/取消/无效回执不释放预留；服务重启不归零预算、不自动重放。SDK取消需实际notification，断开连接不能推断服务已经取消。

`/debug/runs/{run_id}/flow`增加同源、主体/session受限且脱敏的retrievals，包含回执、服务PID及双端校验；未知模型用量标incomplete，超大诊断按原大小边界裁剪。实际usage、charged上界和费用估算分开。真实迁移对照必须用同一次实际Embedding向量，避免供应商两次响应波动混入传输一致性结论；专项探针的观测仅记录原网关返回，正式运行不读取测试向量。省略远端配置保留本地端口，选择远端后失败关闭。

## M21观测关联

原HTTP、工具和Retrieval wire契约保持。TraceEvent/DebugSnapshot新增可空32位hex的otel_trace_id，原trace_id不改；三视图读取仍先用身份/session授权run。观测上下文不是身份、receipt、checkpoint或审批依据。

独立MCP deephelp/observation元数据只含W3C校验的traceparent；不传tracestate/baggage或工具参数。签名身份仍由原deephelp/internal验证。无效上下文新建trace，不拒绝有效身份、不触发调用/恢复。stdio和HTTP均复用此接缝。

审批首个实际pause引用保存在增量dh_m21_observation_links，恢复在原归属校验后创建Span Link；它是可丢失诊断，不改checkpoint或业务账本。隐藏推理、正文、凭据和SQL参数不导出；时钟、并发和指标分母见[观测入口](../infra/observability/README.md)。

GET /observability/runs/{run_id}?session_id=...&cursor=0&page_size=100先沿debug原身份/scope及审批归属授权，再只查询PG登记的原生Langfuse观察；不接收任意trace/search。2MiB/有界超时、200条单页与20000偏移保持；返回结构化span/parent/link、total/next_cursor、私有Langfuse链接、available/unavailable/truncated/incomplete。未登记旧run返回source=legacy-tempo、status=retired、空观察及原otel_trace_id，不查询退役后端。诊断失效不改变receipt、不重试业务，no-store；外主体/session和不存在同为404，未授权不请求平台。

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

强制tool_choice时必须收到唯一、同名且参数schema有效的原生tool_calls。M35为SOP就绪节点显式开启已有repair_once：仅已返回正文、finish=stop且没有原生调用时允许一次格式纠正，保留原消息、工具/schema及已确认参数；第二次仍无原生调用即MODEL_OUTPUT_INVALID。两次API预约与usage计入同一预算，正文JSON不转换成ToolCall或事实；畸形原生调用、错误工具名/类型或传输错误不走此纠正。工具和输出schema不能在同一请求混用，普通请求默认不纠正。

调用者必须共享原 ExecutionBudget，model 调用前有正 token/cost 上限；超时/取消不释放可能已发生的计费预留。ChatResult 保存 usage/finish_reason/provider_request_id 与校验过的 schema/tool 字段，EmbeddingResult 按输入顺序含签名、缓存命中和索引适配标记。模型结果不是业务事实，模型工具调用不是 ToolResult 或 MCP 执行证据；下游仍做白名单、身份/归属和参数核验。接口与版本限制见 [模型矩阵](MODEL_CAPABILITIES.md)。

M31 增加同一 `models.py` 内的 RerankDocument/RerankRequest/RerankHit/RerankResult 与 `ports.py` 的 RerankPort。输入文档为不可变有序 tuple，包含本地 doc_id/content/intent_code/source；仅 query、文档文本及可选 instruct 送模型，top_n 严格为 1..文档数。响应 index 为唯一范围内非bool整数，数量等于 top_n，score 有限且按降序返回，并列按原输入 index 稳定排序；以 index 映射原文档，不采信响应替换内容/标签。score_kind 固定 rerank，无概率/融合含义。完整合法 usage 通过原共享预算结算一次；缺失/不完整 usage 为 null，结构诊断保留已知项，未知尝试保留预留。

同链增量 `RerankPolicy` 绑定版本/模型/instruct/enabled/candidate_budget/top_n/failure_policy及指纹；当前对全部候选重排，top_n=candidate_budget，实际文档不足时top_n为实际数量。`DenseResult.retrieval_mode=hybrid_rerank`/`policy_version=m31-rerank-v1`附`RetrievalRerank`，记录输入顺序、passed/not_run/fallback、原因、原生结果/usage及耗时/attempt。hits仅重排原始融合证据，原分数不变；candidates按重排最佳证据归并截top_k。passed强制原600语义确认，不复用fusion阈值。发布selection支持m31-dev-selection-v1，明确最终top_k与更宽candidate_budget、完整策略指纹；旧selection仍可验证。业务envelope保持原契约。

绑定M31策略时，Retrieval MCP工具metadata、委托和回执采用`m31-retrieval-v1`；未配置策略仍为原`m20-retrieval-v1`。profile同时绑定完整策略、候选上限、最终top_k、模型配置及原语料/signature；新策略请求的top_k必须等于发布值。开启后retrieve返回hybrid_rerank，compare返回原三路及第四路；关闭后的retrieve保留原Hybrid，compare第四路明确not_run/disabled并与原Hybrid候选一致，实际重排0次。服务端是唯一重排执行点，客户端只验证签名、原文映射、来源metadata、排序/意图归并及已知usage；compare的原三路与第四路必须共享同一原始候选。Milvus导入来源结构与原始attributes分别核验，额外属性或外来语料拒收。

RetrievalAction增加rerank，开启回执最多一次Embedding和一次Rerank；关闭最多一次Embedding。只有这两种动作可携带模型调用数，模型attempt/实际tokens须与原生结果一致；预算预留按最宽reference文本及重复query/instruct计算，合法签名回执只结算一次，断连/不可信回执仍保留未知预留。完整发布文件闭包绑定策略指针、相邻corpus.jsonl和对应内容报告的精确profile，不允许向旧清单动态注入新模型。M17明确`--capability rerank`才消费M31指针，M22仅显式提交检索表单产生调用。

| 阶段 | 当前要落地 | 只作未来约束 |
|---|---|---|
| M02 | 请求/响应、实体来源、最小Question、IntentDecision、工具/证据结果、当前版本/预算摘要、目录与fixture | DB语义可以设计，不能称已验证数据库；审批恢复/outbox领取不实现 |
| M06–M08 | 工具协议/SOP实际消费字段；PostgreSQL消息/run/question账本、请求幂等 | MCP工具保持只读；合成写操作通过M15专用执行口 |
| M10–M12 | 生命周期、缓存/向量投影、事件上下文与增量版本 | 只读装配不启用审批；普通消息不恢复WAITING_APPROVAL |
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

EventContext：current_message_id、member_message_ids、candidate_question_ids、selected_question_id、cleaned/contextual_text、entities、conflicts、assignment_decision和证据。跨明确订单的相似传递边不自动合并；明确更正走有版本的替换事件。上下文候选为ACTIVE、WAITING_SLOT，WAITING_APPROVAL仅允许授权读取；结束状态默认不参加活动合并。命中后回查PostgreSQL归属、状态和version，不只信向量投影。

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
- WAITING_APPROVAL/PENDING_APPROVAL 由 M15 显式装配启用，必须绑定 plan/run/operation；普通消息没有批准/resume 字段。未配置 M15 时仍沿 M14 计划移交，详见下文 M15 契约。
- 合成目录/样本/指标定义见应用 `assets/README.md`，固定回归数据不得冒充 M17 未见评测集。

## 状态必须区分

Question：ACTIVE / WAITING_SLOT / WAITING_APPROVAL / RESOLVED / HANDED_OFF / CANCELLED。
Run：RUNNING / WAITING_APPROVAL / SUCCEEDED / FAILED / CANCELLED。SUCCEEDED只表示本轮正确完成，完全可以输出CLARIFY，不等于question已解决。
Operation：PREPARED / IN_FLIGHT / SUCCEEDED / FAILED / UNKNOWN / CANCELLED。UNKNOWN不能被通用重试转为再次写入；先对账。
Approval：PENDING / APPROVED / REJECTED / EXPIRED / REVOKED。批准必须绑定操作参数和版本；恢复执行仍需再次校验授权与状态。

## M15 持久审批契约

唯一 DTO 增量增加 `ApprovalCommand/ResumeCommand/OperationRecord`、操作/审批枚举及 `approval_operation_id`。`Question.WAITING_APPROVAL` 必须绑定操作；pending response 同时绑定原 run、问题、SOPPlan 与等待状态。运行参数、客户端、连接池和锁不进入 DTO。默认 M14 计划仍移交，只有显式 rights-port/key 装配才启用等待。

`GET /operations/{operation_id}?session_id=...&run_id=...` 读取当前主体的事实；`POST .../approval` 接收 session_id/run_id/expected_question_version/parameters_hash/sop_version/snapshot_hash/decision，decision=approve/reject/revoke。审批者取可信身份源，不能从请求正文伪造。`POST .../resume` 仅收 session_id/run_id，不接受新计划、参数、审批值或任意 graph Command。外域 tenant/user/session/run 统一404；未装配返回501，未鉴权401，状态/版本/并发领取冲突409。

`ApprovalRepository.before_finish` 在原终态事务中登记 PREPARED/PENDING、WAITING_APPROVAL run/question 与 M10 outbox；等待不写 finished_at。操作ID主键、run唯一键及 session→question→operation 的短事务锁保证决定/领取串行。dispatch 在同一事务核验主体、问题状态/版本、参数、当前SOP快照、审批/期限、lease并登记IN_FLIGHT；不跨HTTP/人类等待持锁。普通消息不能改待审批问题；拒绝/撤销/过期变为 CANCELLED，问题转人工，后续可重新规划。SOP切版后可在原主体/问题/版本/参数绑定下关闭旧计划，但新批准与dispatch仍须匹配当前SOP；关闭不改写已批准计划。已发送操作不能撤销为“无效果”。

LangGraph使用独立 `dh_m15_checkpoints/dh_m15_checkpoint_writes`，thread=`m15-approval-v1:{run_id}`；只保存操作引用、游标及必要结果。等待、执行、对账分节点，恢复先核对PostgreSQL审批事实，客户端resume值不会授权。checkpoint缺失可从已提交计划重建；业务已完成但checkpoint滞后时按ledger推进终点。

合成下游只接受服务签名和当前APPROVED/IN_FLIGHT操作，再核对合成订单归属/可调整状态；独立短事务保存网络调用与唯一效果，进程重启不丢。UNKNOWN/IN_FLIGHT先按operation_id查询，未知查询不写；明确ABSENT才允许按当前绑定和最多两次dispatch重试。效果绑定摘要、实际成功call_id形成受校验事实；不依赖Redis/Milvus或checkpoint证明业务效果。M16多轮/流程视图从同一PostgreSQL操作与历史附加审批状态，详细trace轮转不删除账本。

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

FakeRepository 的内存用例按 tenant/user/channel/message 作用域保留首次结果；同逻辑消息换 request/trace/received_at 不替换 run，正文/session/occurred_at/question_hint/schema 变化返回 IDEMPOTENCY_CONFLICT。它没有事务、持久化或进程崩溃保证，M08 仍须验证 PostgreSQL 唯一约束。兼容及后续业务验收责任见 `ACCEPTANCE.md`。

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

`Conversation.run(request,budget)`处理当前完整消息，只有600进入IntentService。`MessageLedger.accept`返回新领取或原run/Question/结果，`finish`以原Question version+1及RUNNING条件更新run/Question；同消息payload内容范围沿用既有契约，传输ID不进入hash。PostgreSQL三表的唯一接收与终结事务已由M08消费；相同消息回放使用本次request/trace ID、原run/question与事实结果、当前预算0。RUNNING仅返回进行中引用，不抢占或恢复；不承诺硬中断后的自动恢复。

M08缺槽位提示新消息完整重发，跨消息entities/事件/记忆未启用；question_hint先按主体/session回查，再以INVALID_ARGUMENT拒绝恢复，未授权hint为FORBIDDEN。取消终态保留已发送调用ID，但无成功事实；提交失败不返回ANSWERED。唯一事实模板只消费经M06/M07核验的Fact与配置结论。本机Bearer令牌映射可信合成身份，正文无身份/批准字段；不等于企业登录系统。M01未组装业务端口时的离线501行为保持兼容。

M09保留DenseScope原字段/指纹/集合名，新增其子类HybridScope：必须显式`index_kind=intent_hybrid`，`analyzer_version=jieba-search-cn-lower-v1`，独立`m09_intent_`集合。历史/事件集合不能进入此端口；尚无用户历史召回，不以意图scope替代用户namespace/state权限过滤。HybridRetrieverPort继承DenseRetrieverPort，新增同query `compare`，仍返回同一DenseResult/Hit/Candidate；无第二套领域类型。

Hit/Candidate的score_kind增量支持cosine/bm25/fusion：COSINE有界，BM25非负无界，fusion由归一化WeightedRanker返回。Hit保留raw_dense/raw_sparse/fusion_score/rank/matched_sources；null表示该文档不在该路线的前K候选中，不表示真实相似度为零。每路ANN和最终返回均K，融合联合候选最多2K，不能声称计算量相同。按意图取实际返回样本的最大分数，不按样本数量求和。DenseResult保留旧默认，增加mode、候选预算、权重和本次三路总检索耗时；单路SDK耗时在A/B报告中另列。所有分数不是最终正确概率。

IntentDecision增加可选`retrieval=null`，保存600实际候选/分数/版本；分类结果仍由唯一IntentService给出，原envelope版本和消费者兼容。M09发布指针一次原子替换已验证scope、manifest、完整冻结策略与上一版本；启动核对index/dev/test摘要、权重及候选预算。旧M05指针仍装配原Dense端口，M09指针装配Hybrid，不自动改默认配置。checkpoint/审批/写工具边界不变。

学习环境网关扩展：ChatRequest默认输出2048 token，移除4096输出、32消息与16工具声明的本地上限；新增可选enable_thinking=null（继承ProviderConfig）和thinking_budget=null（继承配置）。ProviderConfig默认不开推理、思考额度8192；思考启用后在原预算预留中另计思考额度。请求/响应默认1MiB/16MiB，可配置更大；max_texts默认128，可配置更大。模型自身能力/错误、任务deadline/累计预算与结构/工具参数验证仍由原网关处理。既有业务调用显式输出参数不改变，消费者继续导入同一DTO。

## M10 多问题记忆契约

`ConverseInput/RequestEnvelope`兼容新增`expected_question_version`：提供时必须有`question_hint`，参与幂等摘要；未提供的旧消息摘要保持不变。无hint新建问题；hint只续接同tenant/user/session的ACTIVE或WAITING_SLOT，先检查版本、未完成run和消息时间。接收续接与终态各增一次question.version。重复消息先回放原run结果，重开后重投旧消息也不再执行工具。普通新消息不恢复RUNNING或审批。

Question新增`unresolved_fields`，保留已确认实体并阻止冲突字段进入工具；`conflicts`保留更正链及消息来源。300复用M04合并，600保留显式问题的已绑定意图，话题不符需新建；自动归属留M11。关闭由SOP事实/流程结果或有证据的用户确认决定，TTL不改变业务状态。

`CaseRepository`读取有界snapshot、归属内question和CAS transition；`MemoryPort.load`返回`MemoryWindow`，包括有channel/message/question引用的历史、开放Question和已结`CaseSummary`。PostgreSQL始终核验事实；向量候选必须同时匹配主体/session、当前status和version，过期ACTIVE不能恢复问题，摘要不写回实体。

`GET /memory?session_id=...`读取当前身份的窗口。`POST /questions/{question_id}/state`接收`LifecycleCommand(session_id,expected_version,target,reason,evidence_source,evidence_ref)`；同主体/session校验、版本冲突409，禁止修改RUNNING问题。RESOLVED要求user_confirmation/process_result，CANCELLED要求operator_cancel，HANDED_OFF要求operator_handoff，开放状态切换要求slot_check；关闭后只允许explicit_reopen→ACTIVE。WAITING_APPROVAL仍禁用至M15。这些是问题状态操作，未接入业务写工具。
## M11 事件归属契约

复用唯一领域DTO，新增`EventMessage/EventCandidate/ClusterJudgement/EventContext/ClusterEdge/EventClusterResult`和`ClusterJudgePort`。`EventAggregationService.aggregate(request,budget)`返回聚合结果，不拥有主意图或SOP。窗口来自同主体/session PostgreSQL事实；开放问题含WAITING_SLOT，已结及待澄清事件不参与自动归属；WAITING_APPROVAL仍由既有M15禁用校验拒绝，不恢复审批。

`ClusterJudgePort.judge(current,candidates,messages,budget)`至多调用一次，返回attach/new/uncertain、候选ID、把握等级、关系和原文引用。只有候选中的目标、高把握及当前/目标两侧精确引用可attach；代码检查scope/status/version、实体冲突、簇级订单和独立问题约束。cosine是候选证据，不是概率；低相似门禁只作保守排除，不据小样本宣称校准接管阈值。明确实体更正是成员更新，图中不作订单等价union。

`PersistentEventAggregation.process`通过M10接受消息、CAS终结和事务outbox保存归属。账本新增`lookup(request)`及可选可信`attribution=(question_id,expected_version)`；存储/幂等hash仍取原始请求，不能把内部选择的hint写成用户payload。scope/开放状态/版本/乱序/未完成run在事务内再验。重复终态原样回放事件图、模型/工具0；未知RUNNING拒绝重领。`ResponseEnvelope.event_cluster`和`Question.event_summary/aggregation_pending`均为默认兼容字段，无新表或依赖。

摘要为有出处的原文摘录，保留实体冲突和更正来源，不能成为新业务事实。多个主诉结果的`current_event_context=null`且要求分条，数据库只保存待澄清消息，不宣称已建多个事实问题。待澄清问题可用明确hint补充；未归属补充不能污染既有问题。独立CLI与显式无持久化preview见应用README，完整500→600接入由M12完成。

## M12 完整主链与级联契约

明确补充订单/券号且只有一个相容开放事件、确实补其缺失槽位时，500按已确认绑定确定性关联，仍检查整簇和接收CAS；不调用归属模型。多个相容事件、更正或一般语义归属继续使用受控裁决。此守卫只消费既有事实，不进行主分类。

同一`Conversation.run`装配`EventAggregationService`后，300提取当前原文一次，400读取PostgreSQL事实窗口，500复用两者归属，再以原始请求payload及可信attribution原子接收。预接收的阶段trace缓冲到真实run/question确定后发布，不产生伪业务ID。已有终态先lookup直接回放；并发接收仍由原账本唯一键/CAS决定，不抢占RUNNING。未授权hint在写业务数据前拒绝。

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

`SOPStatus.NEEDS_APPROVAL`配`NextAction.REQUEST_APPROVAL`及唯一DTO `SOPPlan`。计划绑定operation_id、模拟动作、主体、问题ID/执行输入版本、只读预检参数及hash、SOP版本/完整快照和证据IDs。`response_from_sop`保持HANDOFF/HANDED_OFF/run SUCCEEDED映射；未装配M15时明确未申请、批准或执行。显式装配M15后由Conversation转换为持久等待，重新核对当前问题版本、归属/状态/期限/参数与唯一领取，不能把计划当授权或复用普通消息批准。

## M16 事实回复与调试契约

`ResponseEnvelope.reply_presentation`默认null，旧保存结果/消费者兼容；新SOP回复包含template、mode、reason及fact-reply-v1。模板仅取通过原SOPResult事实/证据校验的字段；缺槽位、失败、人工及仅计划分别输出。可选`ReplyComposer`仅用严格opening/closing枚举选择固定非事实措辞，不接收自由正文；模型不接触金额/编号，非法结构/新增字段/超时/失败回退原模板，外部取消继续传播。同一共享预算计量，1100不重分类或调用业务工具。M14计划明确未申请/批准/执行。

`StageReport.started_at/finished_at`默认null。正常执行记录真实13段起止；治理SOP节点通过任务上下文观察，保存node_id/kind、开始/结束、下一节点、错误及实际工具call_ids。工具观察含已核验参数/hash、child timeout、开始/结束、结构化结果/证据；并发不共享记录。旧ReAct仅有原阶段与工具记录，无虚构治理节点。

终态成功提交后记录一条`TraceEvent.debug_snapshot`，唯一DTO `DebugSnapshot`包含主体/session的HMAC scope、原run/question/trace和已脱敏的显式输入/实体/决策/窗口/归属/固定SOP/节点/工具/回复/预算。输入另记accepted_question_version；投影完成未在本请求观察时明确标记，不把outbox排队当Redis/Milvus已完成。单快照目标上限128KiB，过大非关键段省略并写incomplete；trace不是业务账本，也不存隐藏推理。

`GET /debug/runs/{run_id}/{intent|turns|flow}?session_id=...`按与converse相同身份源鉴权，再匹配tenant/user/session。三个视图投影同一快照；重放run指向原执行trace，新请求计数仍0。跨主体/session与过期/丢弃/不存在均404且不泄露存在性；返回no-store。`export=true`生成JSON附件，危险公式字符串加单引号；CLI仅调用鉴权loopback API，不直接打开追踪文件。

订单/券/消息及主体/session编号用同一本地key生成稳定替代ID，标准凭据键、Bearer/sk及隐藏推理字段遮盖；证据引用保持可关联。span偏移指向脱敏前原文，不能按替代ID长度重新解释。保留`.jsonl.key`以跨资源维持同scope与替代ID；其与auth/budget/pointer/provider不得重合。默认JsonlTrace单写者、128项非阻塞队列、2MiB×4文件和7天期限；超限/磁盘故障只丢非关键诊断，读取返回dropped_events/incomplete或404。PostgreSQL消息/问题/终态及工具审计独立保留，不能随trace轮转删除。MemoryTrace供测试及隔离评测进程使用，不作为服务持久trace。

## M17 评测契约

EvalCase/EvalTurn只描述公开合成gold、split/来源/模板组和同/异事件关系，复用IntentCode/Outcome及DTO；运行仍返回原ResponseEnvelope，不增加业务HTTP字段或修改默认装配。gold标签需确定性语义支持，槽位需同事件原文/前置消息依据，事实需business.json固定hash；训练/索引/开发输入不能伪装新test，regression中的编号变体不算新增独立case。

m17-evaluation-v1报告绑定完整包内容hash与Git状态、数据/模型/检索/策略指纹、固定选择和调用上限。每行保存实际响应、PostgreSQL确认实体/来源、检索及正式MCP ledger，按实际call_id/成功evidence_id核验；缺槽位调用、跨对象、无成功证据回答单列硬失败。五类overall与三支持类指标、消息/会话完成、实体值/来源、实际工具参数/gold工具行为分别统计，空分母保留null和原因。FastText离线未运行不产生真实效果/费用。

同一实验baseline必须PASS；覆盖、消息/会话完成、硬门禁分别比较。行中expectation由冻结数据决定；关闭events的Dense/Hybrid组中自动followup及multi分段依赖未启用的事件上下文，只作诊断，不参与消息完成基线/已知回归门禁。明确hint续接与独立query不能排除。原始意图/完成指标、全部行覆盖/硬失败、会话指标继续保留并检查；启用事件组全部续接/分段严格比较。报告comparison_scope逐组列出实际比较及诊断行，能力配置/行集合变化拒绝比较。真实采样未跑全不称全量，模型随机性不承诺逐字复现。评测不把checkpoint/debug当账本，不更新模型/索引/生产阈值；审批仅在显式approval范围启用。

扩量`m17-freeze-v2`同时绑定业务目录/fixture/注册表hash；EvalTurn新增默认兼容的business_case、hint_event和conflicts。business_case指向确定性事实及Money、工具顺序、终点、错误预期；hint_event只在评测驱动中转为已有问题的真实question_hint/version，不能让gold决定生产分类。冲突确认须当前原文唯一且已确认，旧冲突保留被拒值与实际替换来源，解除未解析阻塞后才允许SOP执行。coupon.status新增already_used/not_started/scope_mismatch/frozen/revoked，usable必须与status==usable一致，旧数据与工具schema保持兼容。

M17审批扩展沿用`m17-evaluation-v1`，新增`approvals`：冻结数据digest/案例顺序、逐操作rows、越权尝试的before/after真实账本、效果/发送/查询次数和独立release_gate。`approval`独立范围routes为空；组合`run --approvals`时只读范围及审批范围都必须通过。baseline必须同一代码/配置/冻结选择，审批列缺失、范围变化或安全失败不能接受。报告不包含服务key/令牌；实际模型HTTP与固定动作恢复协议分别说明。

## M18 反馈契约

FeedbackCandidate复用DTO/IntentCode，只接收专用合成身份与m18-collect的PostgreSQL已完成run；run_id唯一采集，candidate_id稳定，文本/实体/出处先脱敏。observation记录原决策、结果、工具证据和trace，不能证明标签。RouteReview按corpus/fasttext/rule独立记录审核者、理由、方法、标签及拒绝/争议/撤回；独立于heldout的确认必需。确定性晋级只接受已登记的合成来源、组/编号中立文本/语义标签一致，不接受置信分或SOP成功作为证据。

PostgreSQL候选revision做短事务CAS，每修订保留历史、撤销旧未完成任务，为仍获准路线生成candidate/revision/route唯一任务。领取有限尝试/租约，完成需持有未过期令牌；外部导入/CPU训练在事务外。派生路径与实际文件hash逐路线保留，包括语料、向量manifest、训练输入、原/量化模型与规则；更正或撤回使旧审核快照失效，不盲目删除引用工件。

exports只使用获准路线；FastText回流只进train，保留原dev/test；known train/index/dev/test/M17、编号变体及声明来源/近义组不能进入。另三条发布输入与采集组分开固定，程序能检查声明/重叠，不能证明自然语言语义独立。规则为有界字面AND/排除数据，保留原文span与版本，不热执行生成代码/正则。

m18-demo-assets-v1绑定审核快照、三路指针/文件hash、Embedding签名及providers/data摘要。构建新集合，启动完整向量/hash及模型回读；固定M17抽样+独立发布样本内容/安全全通过、审核快照未变化才显式更新本机演示active/previous。装配时读取固定三路文件，原M09业务指针保持原资产。兼容演示入口保持本机指针语义。

完整发布入口使用唯一DTO `ReleaseManifest(format=m18-release-v1)`，保存所有文件hash、完整FastText预处理/词典签名、SOPRegistry及snapshot、包内Prompt摘要、两路模型配置、代码内容/基线commit、审核来源与成功评测摘要；其JSON摘要为release身份。相同version不能改内容。注册/启用前完整远端向量/hash回读，代码内容不符拒绝装配，不能用更新的二进制执行旧清单。DTO不携带连接或锁。

运行/学习边界重整保持上述DTO格式；成功评测报告新增`code_integrity_scope=runtime-v2`与`tooling_digest`。准备发布同时核对运行内容指纹与当前完整评测工具/冻结输入指纹；运行装配核对绑定报告、运行工件及runtime-v2指纹，不加载评测器或dev/test。旧清单/无范围标记的报告不能用于新代码；目录或运行字节变化须新完整清单与内容验收。

`ReleasePointer`由PostgreSQL active表给出channel/revision/active/previous；文件不再决定完整发布入口的active。switch以显式expected revision短事务锁定并CAS，来源行在同一事务核验；相同目标与当前revision幂等，旧revision返回409。发布事件与active同事务提交。prepared工件/远端验证不占用发布事务。

`VersionManifest.release_manifest`默认null兼容旧只读入口；完整发布的receipt事务将问题绑定及run引用一起提交。运行及续接只消费其完整版本，新请求选择当时active。跨版本自动归属在原接收事务回滚后一次返回原版本；终态receipt回放零模型/工具。旧审批按原版本查询/恢复，批准和再次dispatch从当前PostgreSQL active读取SOP核验，UNKNOWN先对账不改原计划。retire只标记无引用版本；active/previous和全部run引用都阻止退休，保留终态审计及共享文件，不提供隐式文件删除。

## M22浏览器会话与历史

M36增加只读 `GET /history/runs/{run_id}/explanation?session_id=...`，返回 `run-explanation-v1`。先用当前身份在PG消息/run/question联合查询核对tenant/user/session/run；跨主体、跨会话和不存在均404，无任意文件/trace搜索。最多读取一个原run及同scope诊断，响应no-store、浏览模型/工具0次。`input/receipt/status`来自原PG记录，`question_now`是当前问题，`question_at_run/entities/event/tools/retrievals`是原脱敏debug快照；缺失为null并写gaps，不以新状态补造历史。步骤名称/用途是固定模板，状态/时间/计数沿原receipt，原意图及cascade理由不生成隐藏推理。原receipt计数与客户端所选重投返回计数分别显示；没有终态回执的取消/运行保留状态，不能推断清理或副作用结果。

原 `/observability/runs/{run}` 有界读回增加 `explanation`：在分页切片前按trace/span去重GENERATION、核对input/output为非负整数，父汇总和reasoning不另加；相同ID内容冲突按unknown处理。已知小计、未知attempt数与完整实际总量分开；平台非available/usage缺失/冲突/无generation时实际总量null。每trace的已观察包围时间不累计重叠，缺endTime的duration_ms为null，不填0；CPU/排队无观测保持null。原状态/页边界/2MiB/20k限制、前序与身份守卫保持。原生导出补本进程PID白名单，旧记录缺失不推造；版本来自原观察，profile未记录显示未知。

`GET /auth/session`在当前loopback/SSH同源个人入口自动建立随机8小时HttpOnly/SameSite=strict会话与CSRF标记；刷新复用有效会话，失效/进程重启自动为同一owner重建。serve必须显式给`--personal-identity` JSON（仅`tenant_id/user_id`，唯一匹配`--auth`现有身份）；缺失/未知/歧义在启动前失败，不取第一条fixture。`POST /auth/login`和`/auth/logout`已移除。浏览器没有访问令牌/身份选择，身份来自服务端配置；正文、X-User和Host不能授权。Host限定现行loopback，Origin必须同源，Sec-Fetch-Site仅允许same-origin/none；浏览器写请求需原会话`X-CSRF-Token`。原Bearer协议兼容且优先，错误Bearer不能回退cookie，外身份Bearer不能创建个人cookie。握手/历史API均no-store。API-only探针可不装配个人UI，握手明确503；不会自动选其token主体。

`GET /history/sessions?offset=0&limit=20`给出当前身份会话数/更新时间；`GET /history/sessions/{session}/messages`返回原ConverseInput、run/status、当前Question及原ResponseEnvelope，同样支持offset/limit（1–50）和next_offset。来源为原PostgreSQL账本，不是trace；跨身份session与不存在同为404。读取不运行引擎/模型、写账本或恢复RUNNING。只有原converse执行绑定message_id和问题版本；浏览器补槽显式传question_hint/expected_question_version，网络重试原样重投。原响应trace与重投新请求trace分别标明，历史阶段不算新调用。根工作台随app包提供，旧三视图保留`/debug`。

### M22 可选学习扩展：协议与工作流

GET /learning/status、/protocol、/workflow复用BrowserAuth；POST /learning/protocol仅benefits/coupon/client_invalid/server_invalid，参数只含订单/券ID；内部固定无效输入不允许任意委托。GET协议实际执行tools/list/initialize，不请求模型。POST /learning/demo只允许显式fixture身份和本机合成执行口。GET /learning/demo/{operation}?session_id&run_id先原ApprovalRepository.scoped再读checkpoint/效果/审计；POST同路径/approval及/resume使用原ApprovalCommand/ResumeCommand，额外限制m22-demo会话及DEMO-D01。无任意线程/SQL/文件/工具名或checkpoint写API。图定义仅M15审批部分，不冒称整个13段是LangGraph。

### M22 学习扩展：实体、三路与记忆

POST /learning/rules只收非空text（最多2000字符），原规则输出带原文范围/否定/更正，模型0次、不表示600决策。POST /learning/retrieval相同输入边界，仅使用装配的Retrieval MCP和scope，固定top3、原同向量compare、持久budget_factory及request_execution；返回原候选、签名回执、动作/耗时和实际usage，不能传scope/模型/路径。GET /learning/memory?session_id先PostgresHistory.messages主体验证再只读权威窗口/本generation Redis/outbox末50条；缺失投影不重建，外身份404。意图、归属、运行观测页继续原/debug/runs/{run}/intent|turns|flow及/observability/runs/{run}，同session/run守卫，不另分类或任意trace搜索。

### M22 学习扩展：评测与飞轮

服务器显式设置DEEPHELP_LEARNING_CATALOG才装配`/learning/evaluation`；只允许该受控配置中的owner，其他主体读/写均404。GET根目录、/reports/{id}?offset、/candidates/{hash}、/assets/{id}、/releases/{hash}、/plan及/tasks/{id}只读；报告ID/资产ID映射服务器文件，浏览上限8MB、差例每页20，目录最多16报告/16资产。字段投影保持冻结范围、分母、分数、差例和适用边界，不暴露账号/模型密钥或累计费用原始JSON。

POST /capture仅原专用合成m18-collect已完成run；POST /candidates/{hash}/review绑定原revision、route、人工review和独立confirmed，审核者取受信owner。批准仍走原留出隔离/独立标签/规则约束；撤回全部路由也复用原store。不提供任意SQL、训练输入或checkpoint写入。

POST /tasks仅audit/offline/build/validate/prepare及明确candidate_ids/asset_id/confirmed；重任务同进程串行、1800秒、最多3次尝试/100记录，超出或冲突关闭。GET任务的state与result.evidence_status、部分证据独立；硬退出后标INTERRUPTED，POST /tasks/{id}/cancel|resume明确取消/恢复。取消等待原适配器关闭；恢复新不可变尝试路径，已提交/未知提交的派生、验证或版本登记先对账，原M18任务lease仍生效。构建/验证复用已打开的应用预算writer，仅显式启动消耗模型；已知不足构建Embedding批次的预算在启动前拒绝。构建PREPARED、验证PASS均不改active。

prepare调用原prepare_manifest/ReleaseStore.register。POST /release收publish|rollback、target完整hash、revision、confirmed，只切配置的单一channel；rollback额外核对当前previous，原switch重验字节/验证/审核/保留资产和CAS。历史PASS不表示当前发布资格。基础app不反向导入此工具扩展。

### M29 历史平台映射

`EvaluationCatalog`增加可选`platform_config`与最多16个`imports`；每条只允许服务器持有的report/data/manifest路径、1–20个case和1–4个mode。GET `/learning/evaluation/imports/{id}`先原owner守卫、后审计冻结来源与读取持久状态；来源变更409。POST原`/tasks`增加`platform_upload`及受控`import_id`，仍要求confirmed；其他action不接受import_id。应用业务观测与上传SDK分别拥有进程资源。COMPLETE必须逐行完整读回，与原历史质量门禁独立；部分/取消/中断不显示全量通过。平台Score不会更改候选review或M18 active。

### M29 不可变Prompt与审核依据

原ReleaseManifest DTO形状保持：`prompts`新增保留键`langfuse:intent-fallback`，值为本地`m29-prompt-v1`工件SHA256；`files`必须包含唯一对应文件及hash。工件固定name、正整数version、静态system全文、原请求contract和request_digest。当前契约无模板插值，完整输入仍为text/confirmed_event_context/retrievals，schema/参数/tools由代码核对；模型绑定原judge_providers_digest。原内容报告必须绑定相同请求摘要，不同文本/契约拒绝；相同请求的不同平台版本仍各有完整release hash。验证了自定义文本却遗漏工件同样拒绝。运行VersionManifest.prompt记录name/version/请求摘要前缀，只读取清单的本地工件，无平台标签热切换或故障降级。

`EvaluationCatalog.prompt_artifact`可选服务器路径，原validate/prepare复用。验证只写任务报告，不覆盖被完整版本引用的资产验证文件；准备绑定所选资产最近验证任务，只有SUCCEEDED/PASS继续，原精确门禁再核验；稳定版本名绑定准备任务ID，避免同资产的不同Prompt冲突。GET `/candidates/{hash}/platform`仍先owner；导出核对整份候选revision摘要、结束观察、输入/输出及trace/observation。POST `/tasks`增加`platform_candidate`，仅一个已知candidate_id及确切candidate_revision、confirmed；其他action不接受该revision。平台非幂等Prompt/评分配置POST结果未知不自动重发，GET/确定性映射继续有限重试。SDK写入独立子进程，原取消/恢复槽位守卫保持。

POST原review可附`platform_score_id`，只允许v3 Score的ANNOTATION、有作者、当前revision具体observation、m29_intent_label/CATEGORICAL支持标签。v3 subject/value/annotation字段显式适配；trace级、API/EVAL、数值高分不成为独立标签。选中score与本次独立label必须一致；仍要求人工review、留出隔离、reason、confirmed，审核者来自owner；原事务CAS防读回后的revision竞争。审核理由绑定score ID/摘要和原revision。平台读回/导出均0模型，不自动review、派生或发布，修改revision后旧证据失效。


## M40 受控专业Agent委托

`ConverseInput.execution_mode`默认为single，显式collaboration才启用；server仍须装配已登记角色资产。默认single不改变旧message payload_hash，显式模式进入原hash，重投不能换模式。`VersionManifest.collaboration`为角色/策略完整摘要，默认null兼容历史记录；当前问题绑定与安装资产不符拒绝。

唯一`AgentTask`固定task_id（父run与SOP节点生成）、parent_run/question、主体/session、question输入版本、角色/version、策略摘要、goal、已确认Entity与来源、单工具许可、参数、有效版本及原budget_ref/派发快照。模型仅建议当前delegate_task，不能正文注册角色/URL/代码或扩权。`validate_agent_task`回查全部可信上下文，深度限1，任务数有界。主分类仍只在600，子任务不能改问题/终态/审批。

专业角色独立指令与上下文，以原生工具建议进入原SOPExecutor._lookup（schema/参数/实际call/证据及共享计量守卫），成功后仅return_evidence原task/status/实际全部证据ID。`AgentReceipt`保存公开模型动作call/provider/model/已知usage、实际ToolResult、起止及COMPLETED/NEEDS_INPUT/REFUSED/FAILED/CANCELLED/UNKNOWN；没有隐藏推理。模型结论不是事实，证据回收失败不输出已解决。原SOP继续跨源冲突/分支/审批，权限只能收窄。

`SOPResult.agent_receipts`与`ResponseEnvelope.agent_receipts`默认空兼容旧消费者；终态随原PG response事务保存，回执必须属于同run/question、固定策略和已记录工具call。`run-explanation-v1.agent_receipts`直接投影原receipt，原页面读取0模型，不用诊断或新的模型故事补回执。取消继续向父请求传播，异步资源由原适配器拥有，未终态任务无回执时保持未知，不自动重领RUNNING或恢复旧审批。

M40有界协作：角色策略agent-policy-v2登记order-benefits/get_order_benefits与coupon-status/check_coupon，depth=1、max_tasks=2、每任务max_task_attempts=6（含网关修复/工具重试）、task_seconds=80、total_seconds=90。原ExecutionBudget不复制额度；同请求顺序委托。dh_m40_agent_tasks外键原run，唯一(task_id)/(run,node)/(run,query_hash)，原消息hash、双侧主体/session/输入问题版本通过后短事务领取，claim_token CAS单调更新。终态不可改写，已完成同绑定只读复用；未完成/UNKNOWN/取消不得重领。原终态消息仍按原账本重放。

原GET history/runs/{run_id}/explanation保持先PG归属鉴权；新增agent_tasks及agent_tasks_source读取实际持久进度，不返回claim_token、不消费模型或工具。agent_receipts仍来自原response，两者不能合成为伪造终态；任务表不可用标unavailable/gap。空列表代表确无任务，null代表未配置/不可读。普通会话不提供任意RUNNING恢复或旧审批授权。


M40正式发布保持m18-release-v1/hash格式，角色策略JSON精确字节额外进入prompts，内容报告必须核对collaboration_policy_digest；runtime-v2包含应用与同源built，tooling另绑。单路径默认不变，协作内容门禁适用范围见M40交接。M36旧DTO严格拒绝M40新字段，因此旧安装恢复只读旧记录，新记录保留原值且需回M40读取；不把新回执降格、修改旧manifest或覆写预算换兼容。


## M43 SOP作者草稿

`GET /learning/sop`读取当前GovernedSOP注册表、有限工具/事实元数据及当前owner草稿，模型0。`POST /learning/sop/drafts`只收sop_id/base_hash；`GET /drafts/{id}`回读持久原件，`PUT`只收正revision/content。随机ID不是路径或授权；BrowserAuth同源/CSRF及catalog owner均检查，外主体404、基准/revision冲突409。服务端根由catalog.task_root/sop确定。草稿包含owner、不可变完整基准/base_hash、revision、content_hash、DRAFT状态及原GovernedSOP校验结果；无效中间图可以保存修正，不能作为可执行或发布候选。槽位/意图/稳定SOP ID保持，工具参数仍由原已确认实体与schema生成，不开放脚本/地址/任意表达式。保存不改变正式registry、旧Question或审批/UNKNOWN；短同步writer不跨await，原子replace与锁内CAS只证明单机文件持久协议。

M43预演：POST `/learning/sop/drafts/{id}/rehearsals`提交已保存revision、mode=offline/live-readonly、有限required_slots、合成scenario及真实模式confirmed=true。未知槽位、无效图、旧revision、含审批proposal在任何Port前拒绝；真实模式同时核对原base与已打开模型/只读MCP/累计writer。GET同路径列原任务，GET `/learning/sop/rehearsals/{id}`读取，POST `/{id}/cancel`取消并join，均沿原owner/BrowserAuth。读页面不恢复任务；进程启动未结束记录标INTERRUPTED。

GET `/learning/sop/drafts/{id}/diff`只读保存后的字段差异、完整candidate_hash和base_current；无效图不能形成候选。POST `/{id}/review`须revision、当前base_hash、approve、非空reason及confirmed=true；锁内CAS与新SOP版本校验，生成包含完整base_registry/registry、owner/reviewer、摘要和理由的不可变m43-sop-review-v1原件。APPROVED/REJECTED草稿冻结；隔离RegistryStore防版本复用，不切正式active。外主体404、旧基准/revision/已审核/版本复用409。

原评测JobInput新增可选sop_draft，仅validate/prepare接受服务端草稿ID，拒绝路径/未审核/旧基准；内容报告与prepare按同asset/Skill/SOP选择。六叶子验证复用原catalog_probe的三个固定相邻样本及原预算writer；GET不派发。完整ReleaseManifest新增可选sop_review_path，None在release_hash中省略保持历史身份；有值须prompts的sop:review、files原件hash、完整SOPRegistry与内容报告sop_review_digest匹配，缺失或漂移拒绝。准备登记完整READY，原发布接口仍以完整hash和PG revision CAS切active；旧Question/SOPPlan/审批/UNKNOWN继续使用原版本和原receipt，不因作者审核或发布获得业务批准。

任务固定完整候选registry/hash、draft_hash/revision、可信owner和作者确认槽位来源，结果保存实际run/question、原节点起止/规则/路径、工具/证据/预算、未运行项。LearningJobs.SUCCEEDED只表示runner返回，须另看SOP结果RESOLVED/WAITING_SLOT/FAILED/HANDED_OFF；CANCELLED只有清理结束才返回。离线固定建议/合成事实不是模型质量；远程model_calls=0、业务写入0。真实预演借用原Port与共享预算，只进入原1000执行器，不创建会话账本/审批或调用600/回复润色。预演只验图，Skills组合关闭且正式内容门禁另核对原Skills路径。

## M41 版本化业务Skill

唯一SkillReceipt记录原run/question/node、registry_digest、稳定skill_id/version/digest、已确认Entity来源、状态/起止、实际ToolResult与安全错误；SOPResult/ResponseEnvelope.skill_receipts默认空，VersionManifest.skills默认null兼容旧记录。响应守卫核对原run/question、注册表和call。Skill不是审批授权或事实来源，输出仍验证原ToolResult及证据链；缺槽无模型/工具/Skill，取消抛回父请求，不以新组合重跑恢复UNKNOWN。

SkillDefinition机器契约只允许既有意图工具授予，schema必须匹配原输入/事实；代码选择当前意图/工具的唯一登记候选，ID/version/hash精确读取，同版本字节不可替换。没有远程Skill安装、Markdown代码执行、模型注册或扩权。旧问题skills=null继续原SOP；新版本漂移在任何模型/工具前拒绝。客户端/预算/连接由原请求装配拥有，回执仅值。


M41组合使用SkillStep精确子ID/version/hash与输入来源映射，SkillComposition固定机器输入/输出、意图/工具许可、有限步数/深度/attempts/时限。只允许直接只读叶子，递归组合/环拒绝；校验前序类型与完整依赖闭包，权限取父/意图/工具交集。单模式券诊断先订单→券，原SOP节点只读回收观察；组合与子共享同一ExecutionBudget/原重试上限，父id/children/input_sources/evidence_ids进入原SkillReceipt。审批SOPPlan.skills_digest绑定原问题版本，validate_binding同时核对；未开放写Skill，无新审批/UNKNOWN恢复协议。

`/learning/skills`沿原catalog owner/BrowserAuth；GET详情和`/{digest}/diff`0模型。POST `/candidates`只收base_digest、已知skill_id、新version、goal、confirmed=true；依赖组合形成新版本。POST `/{digest}/review`收正revision、approve、reason、confirmed=true，短文件writer内CAS审核并保存不可变原件；不切业务active。客户端不可传路径/工具URL/代码。原评测JobInput.skill_candidate仅validate/prepare可用，必须选择已审核候选；prepare只接受同资产/候选的最新成功验证。浏览器试运行走原converse，新session/message产生实际行为，未知启动先核对pending，不自动重发。

ReleaseManifest新增可选skill_registry/skill_review_path，空值在release_hash中省略以保持历史清单身份；有Skill时prompts保留keys `skills:registry`/`skills:review`，files绑定审核原件。内容报告skills_digest/skills_review_digest必须精确匹配审核与快照；模型/工具观察沿原runtime-v2/PG回执。生产只用清单快照；旧无Skill清单不启用默认Skill，管理明确501。运行时目标说明进入受信机器Skill系统消息，原工具schema/参数/事实许可不变；选择仍为确定性匹配。
## M42 版本目录与资格事实

complaints-v1保持原三叶子；complaints-v2增加ACTIVITY_ELIGIBILITY_QUERY，父ORDER_QUERY、必需槽位ACTIVITY_ID、只读CHECK_ACTIVITY_ELIGIBILITY。目录须精确包含该版本code集合，拒绝重复/错父级/槽位/未知版本；SOPRegistry.catalog_version明确绑定完整可执行叶子和对应schema_signature，拒绝漏SOP及跨意图工具扩权。原v1序列化省略新增默认字段，旧SOP/corpus/参数hash与discovery保持；scope/Question/IntentDecision/CorpusRecord不能把新叶子塞进旧版本。

新ToolParameters只有activity_id；身份/URL/order_id/coupon_id或额外键在双端拒绝。旧工具继续要求order_id、保持coupon_id原语义及原两键hash。Business MCP按显式catalog_version发现工具，默认complaints-v1；新应用和服务目录必须一致。SOPPlan仍只允许原ORDER_ID写提案，资格查询不创建报名/扣减/退款动作。

BusinessEligibilityInput包含activity_id、eligibility=eligible/ineligible/unknown、remaining_slots非负整数或null、reason非空；新增dh_m20_eligibilities以tenant/user/activity为键并引用同主体活动。POST幂等创建、PUT expected_version CAS与原维护鉴权相同。查询在原REPEATABLE READ事务中读活动/资格并保存不可变evidence；缺活动NOT_FOUND，缺资格为unknown，活动有效期/停用覆盖资格。六个TEXT事实activity_id/activity_label/activity_rules/eligibility/remaining_slots/eligibility_reason全部精确绑定原call、locator与content_hash；名额文本unknown代表未维护，0代表已满。只读结果不保证报名成功；原receipt/历史重读0调用，资料更新不改旧证据。

complaints-v3显式六叶子，新增ORDER_CREATION_FAILED（父ORDER_QUERY，attempt_id，get_order_creation_failure）和REFUND_COUPON_NOT_RETURNED（父BENEFIT_ISSUE，refund_id+coupon_id，check_refund_coupon_return）。v1/v2叶子集合与discovery原件冻结，新工具拒绝order_id/activity_id/user_id等额外参数；原两键参数hash及活动单键hash保持。SOPPlan拒绝attempt_id/refund_id，未开放退款、下单重试或发券写动作。增量登记只比较旧已登记SOP的版本重用，保留旧快照/回退。

业务维护增加attempts/refunds/coupon_returns三种资源与042_complaints.sql三张表，均按tenant/user/record隔离并沿原POST幂等、PUT expected_version CAS。退款记录引用同主体订单/券且券属于该订单；回退记录引用同主体退款/券。同主体来源关联矛盾允许保留原记录，查询返回conflict，不覆盖来源或推定退款完成。旧表/旧行/evidence不修改。

尝试查询精确TEXT事实attempt_id/attempt_status/failure_code/failure_reason/follow_up；退款查询精确TEXT事实refund_id/coupon_id/refund_status/return_status/return_reason。退款状态pending/completed/failed/unknown；券回退pending/returned/failed/unknown/conflict。关联矛盾或退款未完成而券声称returned优先conflict；缺回退行unknown；returned必须关联正确且退款completed。查询在同一REPEATABLE READ事务读取版本并保存不可变证据，NOT_FOUND不能泄漏外主体存在性；模型仅选允许工具，原因从PG维护记录来。

## M44 语料作者协议

`/learning/corpus`复用BrowserAuth/CSRF/同源，唯一catalog owner之外404；无原PG池501。GET按offset/limit分页，PUT/POST/DELETE正文revision与真实PG聚合CAS不符409。record复用CorpusRecord合法叶子/split/固定目录，source是有界非空来源说明；owner、审核者、路径不由正文指定。相同ID仅PUT修改，不允许改ID；修改恢复DRAFT。草稿删除不影响发布、审批或业务事实。

`POST /previews`接受有界base64原字节、jsonl/csv/xlsx与显式列映射，复用原解析器，保存hash/逐行诊断；`POST /imports`必须confirmed=true及server preview_id，锁内再次校验完整目标，一次提交全部行与IMPORTED/imported_revision回执，失败entries/revision均不变。响应未知先读回执，不自动重投。浏览/解析/审核/导出模型0；导入不自动审核。`POST /reviews`明确确认、合法所选ID及理由，PG保存主体、时间、记录revision/hash和APPROVED/REJECTED；历史审核不被编辑覆写。导出只是作者JSONL，不代表正式数据/索引激活。

`POST /versions`要求confirmed、revision及新版本名；每条记录的人工审核主体/revision/hash必须与当前记录完全匹配，PG冻结完整审核闭包和JSONL源hash。相同owner/目录/版本名的同字节重试幂等，不同字节409。GET版本只读本owner历史；修改/删除草稿不修改快照。导出/冻结需为metadata.source保留一格，不静默丢弃已有属性。

原`/learning/evaluation/tasks`增加`corpus_build`与`corpus_compare`，只接受服务端冻结version_hash，compare才接受有界非空query。构建由catalog配置独立dev/test、原模型签名/检索配置和唯一重任务/预算writer决定，不能提交集合、文件路径或向量。候选集合与pointer独立于正式active/previous。原DenseImporter在模型返回后、下一await前保存pending向量/hash/usage回执，写入未知可按同PK续跑；已完成ID仍读回核对，篡改/缺失不能借恢复PASS。旧清单缺逐批回执时保持未知，不补造。

比较要求正式/候选同签名、K、权重、重排配置；一次EmbeddingResult进入两个原HybridRetriever，保留向量hash、实际Embedding attempts/usage、三路原结果与各路服务耗时。未配置Rerank明确not_run/not_configured；分数不是分类概率、召回不是最终意图/事实。GET任务只读原manifest/部分观察；取消等待资源退出，重载后显式resume，完成观察不重跑模型。

`POST /feedback`要求工作区revision、原candidate_id/精确candidate_revision及confirmed；只读原PG候选，corpus路线必须当前owner人工批准、明确独立于heldout。服务器派生文本/叶子/原run/来源组，进入DRAFT并保留完整反馈revision；再经作者审核后才能冻结。普通编辑移除旧反馈绑定并重回DRAFT。`GET /versions/{hash}/diff/{base}`只比较同owner/catalog不可变快照，返回added/removed/changed/unchanged及model_calls=0。

原重任务增加`corpus_gate`，分类消费已完成同向量观察及当前policy，逐条保存/恢复，代码、配置和输入变化409。门禁路线由原RetrievalBinding.mode决定并与候选pointer复核，不能由浏览器或盲测结果选择；该正式路线dev/test指标非退化、八test完整分类及未知范围均正确后才准备原assets，返回task资产ID。Dense/BM25及已配置Rerank的全部差值继续展示，诊断下降不能被标为改善。validate/prepare必须同corpus_version/资产，沿原六叶子业务与两未知内容验证及同SOP/Skill，prepare匹配最新同目标PASS。

语料内容任务修复后仅复用相同runtime、provider、pointer、assets、corpus、SOP及Skill指纹的完整成功行，每行必须已有PG历史与零调用重投断言；失败或未完成行重验。未知意图不执行SOP，SOP快照为空，同时语料dataset/signature仍须匹配。合并报告保持原FAIL与修复part原件，来源hash纳入完整发布文件，不重复计算旧行的本次用量。

原assets可带`corpus_binding`：绑定作者审核、JSONL、索引/集合、合同、冻结split/selection、检索及分类门禁的精确文件hash；完整清单files/source照原合同保护。runtime独立重算必要检索/分类覆盖条件，完整报告必须phase=release且六叶子/未知八场景及事实/重投检查齐全；反馈采集、召回PASS或漏样本均不能替代。ReleaseStore提交时仍锁读原审核revision与active CAS，撤销不自动删除历史或重放业务。旧ReleaseManifest格式/hash兼容，无新可选字段改写历史身份。

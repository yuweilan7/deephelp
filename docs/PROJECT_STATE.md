# 项目当前状态

更新：2026-10-03。只记录实现与实际验证；命令见 [ROADMAP](ROADMAP.md) / [LOCAL_SETUP](LOCAL_SETUP.md)。

| 范围 | 状态与证据 | 限制 |
|---|---|---|
| 工程 | Python3.14.7；根uv workspace、单一uv.lock；modules/deephelp-app | uv须满足根pyproject要求，本机启动器见LOCAL_SETUP |
| P00 | MySQL SELECT、Redis鉴权PING、Milvus元数据读取均PASS；报告 `.local/infra-health/client-health.json` | 只读连接验证，不是容量/业务检索/full复验；[历史交接](../handoffs/P00.md) |
| M00 | DESIGN_ACCEPTED；[交接](../handoffs/M00.md) | 设计基线，不是已实现业务系统 |
| M01 | IMPLEMENTED_OFFLINE_VERIFIED；[交接](../handoffs/M01.md) | 默认离线入口保持501；业务入口见M08，真实网关见M03 |
| M02 | IMPLEMENTED_OFFLINE_VERIFIED；[交接](../handoffs/M02.md) | `0.2.0-m02` 唯一类型、三类意图、36 条合成固定样本；无模型/云库/审批执行，非业务闭环 |
| M03 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M03.md) | chat/schema/tool/embed分别实测；1024维签名、有界缓存/预算/诊断；工具协议合成，不是MCP或业务闭环 |
| M04 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M04.md) | 完整原文/分段、Regex→API→可选强模型、证据/更正/规则候选；300不确定主意图，非业务闭环 |
| M05 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M05.md) | 版本化合成语料、真实1024维Embedding/Milvus、Dense候选/独立dev评测；不是最终分类或业务闭环 |
| M06 | IMPLEMENTED_LIVE_VERIFIED | 本机正式SDK stdio发现/调用、两个合成只读工具、鉴权/取消/退出；[交接](../handoffs/M06.md)；非企业下游或业务闭环 |
| M07 | IMPLEMENTED_LIVE_VERIFIED | 三份版本化SOP、受控执行器、真实模型+stdio内容/ledger；[交接](../handoffs/M07.md)；M08复用执行端口 |
| M08 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M08.md) | 三类单消息闭环、MySQL唯一接收/终态、真实模型/Milvus/MCP、13段trace；业务数据合成 |
| M09 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M09.md) | 原生中文BM25/Dense/融合对照、600接入；12条索引、18 dev、24 test合成样本，融合未全面胜出 |
| M10–M14、M16–M18 | NOT_IMPLEMENTED | 规格存在；按用户启动的特性顺序实施 |
| M15、M19 | HARDENING_NOT_IMPLEMENTED | 可后移，业务写工具前必须M15 |
| M20、M21 | OPTIONAL_NOT_IMPLEMENTED | 不阻塞客诉核心路线 |
| 模型/业务联调 | MVP_LIVE_VERIFIED；企业业务 NOT_RUN | 选定两模型四能力及M08内容通过；真实服务/合成业务，非企业接口 |

M01已有客户端生命周期、deadline/预算/取消、错误/trace、fake Ports和三个异步实验。M02已原位扩展唯一类型，增加实体来源/更正、意图候选/策略、事实/工具证据、归属/参数/预算守卫和状态映射；目录有三类 actionable 意图，合成样本 reference/dev/regression=6/12/18，业务 fixture 有13订单、4券、1活动。缺订单演示为 CLARIFY/WAITING_SLOT/run SUCCEEDED/工具0。M03增加ChatPort/EmbeddingPort、严格schema/原生工具字段、调用/token/费用预留、错误分类、有界LRU和结构诊断；全零Embedding index按选定模型显式适配，normalization=l2、revision=null。未安装LangGraph；默认M01离线入口保持501，M08显式组装真实模型与MySQL三表。

M04新增兼容 DTO TextCleanResult / ExtractedEntity / RuleMatch / TextEntityResult。清洗只规范全半角ASCII及空白，原文、否定、金额/日期/数量、表情和尾部实体保留；Regex扫描完整原文，模型只补缺失/冲突字段。提取值必须匹配原文跨度、字段语义和白名单；多值不选最后一项，明确更正保留旧/新消息来源，模型不能覆盖已确认值。模型分段未完整覆盖或后段失败时，保留观察并澄清，不确认局部结果。三类版本化规则只提供候选；demand_type只描述话语功能。22条合成黄金样本及强模型端口内容验收通过，真实接口使用qwen3.8-flash/qwen3.8-max；不部署本地7B，不宣称原文推理时延。API未知/冲突保持未解析，预算/timeout/取消有界；M08复用该实体端口。

M05新增CorpusRecord/DenseScope/DenseHit/DenseCandidate/DenseResult及DenseRetrieverPort，版本集合隔离、幂等小批导入、未知提交续跑、逐条FP32向量hash与完整回读、本机版本切换/回退。只有reference/train入库，默认6条reference、12条独立dev：文档Recall@1=11/12、Recall@3=12/12，候选Recall@1=11/12、Recall@2=12/12；M02-028首位错误保留，未设最终接管阈值。合成dev不是未见test；qwen3.7-text-embedding-flash签名revision仍null。PyMilvus2.6.17加入应用及根锁。

M06新增正式mcp2.2.0依赖、两个M02命名只读工具、ToolPort/ToolGateway、受控stdio服务和独立smoke。身份/参数/版本/调用ID通过HMAC内部信封绑定，服务端再次检查订单/券归属；模型参数无身份/签名/故障开关，写工具拒绝。共享预算、排队deadline、单工具timeout、64KiB返回体与有界ledger；坏结构/矛盾不发布事实，注入文字只作数据。真实协议下金额/券状态/证据、双用户同进程并发、跨用户/租户、无签名/篡改/重放、故障、取消后会话复用和退出均验证。ledger为合成调用记录，管理resource需签名；不是副作用账本。M08已用固定合成身份Bearer入口接入converse；企业登录鉴权未实现。

M07新增三份JSON SOP/schema、版本化Prompt、SOPExecutorPort/受控执行单元和固定模型回放。已分类Question和可信context先做归属/版本/状态检查，缺失或冲突槽位WAITING_SLOT、模型/工具0；模型选择原生获准动作，观察后固定finish_sop，代码核对参数、对象、证据hash、分支和结束条件。优惠未到账无法确认原因时转人工，券可用只确认查询状态。循环/总时限/模型及工具子时限/单次与总重试有界，复用原预算；只重试明确限流/下游不可用只读失败，外部取消传播。ToolRequest可选timeout_seconds、运行时on_dispatch和失败调用ID保留兼容回归，成功证据只能指向实际调用；无新依赖/迁移。qwen3.8-flash+正式SDK stdio的9条内容路径、注入、失败及ledger/退出通过。空活动分支曾返回文字，已收紧原生结束动作并全量复验；原始诊断/预算留.local/m07。执行器不重新分类/写问题状态；主会话与状态落账由M08接入，LangGraph未启用。

M08新增单条完整问题Conversation及唯一600 IntentService、MySQL消息/run/question账本、本机Bearer入口、CLI/单页调试和完整验收。500显式passthrough；规则单类选择，否则Dense+一次schema确认；无未经校准接管阈值。缺槽位CLARIFY/WAITING_SLOT/run SUCCEEDED、工具0；未知/无SOP正常转人工，1100事实模板不输出原始模型/工具指令。接收唯一键、payload冲突、终结CAS、取消落账、发送ID、8并发唯一接收/跨用户隔离、新连接池RUNNING不抢占和应用资源重建后回放通过；终态提交失败不返回已解决。新增aiomysql0.3.2/PyMySQL1.2.3；Python/根workspace/单锁/infra保留。

M08真实HTTP十条内容路径通过（21模型尝试、9工具调用），12条原始固定样本真实验证意图/参数12/12，旧outcome11/12；M02-001无依据确认未到账原因而转人工。完整36条离线回放只是串联验证：标签36/36、参数24/36、旧outcome20/36，8 ANSWERED/18 CLARIFY/7 HANDOFF/3 ERROR；16差异保留，12个字段语义不足澄清、3个下游归属拒绝、1个M07保守转人工，不是全样本模型质量/业务成功率。原始报告在.local/m08。跨消息补槽/事件合并、Redis投影/记忆、完整级联、LangGraph、回复模型润色、审批/写工具和企业下游未启用；RUNNING硬中断不自动恢复。未跑P00 full或云服务/主机重启，资源重建不冒充这些验证。

最近全工程无外部网络回归：Ruff检查/格式、mypy退出0；Python3.14.7下 **519 passed、1 skipped**，2845条pytest-asyncio policy API弃用警告；M08新增63项、M09新增34项用例，原M01–M07消费者保持通过。integration包含合成ASGI/loopback HTTP及正式SDK本机stdio子进程，e2e验证组装/退出，默认pytest不代表云端业务e2e，M08显式probe另验真实云服务/合成业务。M02–M07无业务库迁移；M08增加专用三表且实际验证唯一约束；审批等待类型/写工具禁用。CI配置存在，本机结果不冒充远端CI执行证据。

LOCAL_SETUP指向本机凭据、模型摘要和连接结果；调用前实时核对可调用性。Windows入口统一py -3.14 -m uv，解释器3.14.7、uv0.12.13；根workspace和单一锁保留。模型选用遵循[AGENTS](../AGENTS.md)的学习效果优先原则，允许使用候选池和账号余额；当前网关仍为固定模型配置，自动切换未实现，默认不开启live。短输出、请求大小与推理模式限制见[模型矩阵](MODEL_CAPABILITIES.md)，业务质量对照未执行。

M04/M05实际探针、累计运行上限及原始报告分别留.local/m04、.local/m05；默认演示离线。M05两个专用集合实际删除/恢复、过滤、版本切换/回退与服务重启持久性均通过。重启暴露Milvus内嵌etcd选举启动故障，冷备后复用原元数据改为同机独立etcd3.5.23，健康后启动Milvus2.6.23；两种真实重启及全量记录/向量hash与查询通过，MySQL/Redis未重启。云端cap总6016MiB/3.75CPU，etcd无主机端口；未验整机重启/高可用/大规模。机制和回退见ADR021/OPERATIONS，维护授权已按用户要求写入AGENTS。M06协议报告留.local/m06，只启动本机合成服务；本轮未调用模型、业务库或P00 full。

原始P00报告、旧安装提示、一次性生成工具和上游参考副本已移出当前工作树，保存在 `.local/archive/context-cleanup-20261002/` 和Git历史。运行配置、运维/验收工具、原PDF、M01源码/测试和模块规格保留；本次未启动下一M。

M09复用M05导入/manifest/容量门禁及唯一DTO，新增独立HybridScope与HybridRetrieverPort。真实Milvus2.6.23/PyMilvus2.6.17执行Jieba中文Analyzer、BM25 Function、SPARSE_INVERTED_INDEX及WeightedRanker；导入/查询分词同版本，保留不/未/not与前导零订单，SKU-A7按sku/a7拆分。新集合不修改旧Dense集合；原始cosine、BM25、融合分数/来源/rank进入600检索证据，不作为正确概率或接管阈值。M08显式传M09指针后接入同一主流程，旧M05指针兼容；默认仍指向M05，未自动推广最佳检索方案。

M09固定12条reference入库、18条dev选择三档权重，选中Dense/BM25=0.5/0.5；24条test不参与选参。test的18条单类查询：Dense/BM25/Hybrid Recall@1=16/18、18/18、17/18，Recall@3均18/18，候选MRR=0.9444/1/0.9722；24条含无关/多诉求的后续600分类为22/24、23/24、22/24。最终Hybrid比Dense多命中一例，仍退步一条券口语查询，多诉求分类错例保留；小合成组不证明企业效果或融合必胜。真实删除后检索缺失、原FP32哈希恢复、版本切换/回退、错误scope/signature过滤与旧M05全量回读通过；真实HTTP十条路径通过。LF重索引后保留原策略，真实Embedding向量哈希与Dense排序复验有波动；最终指标来自当前集合复验。原始对照/资源/诊断留.local/m09，未运行P00 full、历史/事件集合检索、服务重启或M10。

## 后续真实门禁

- 模型调用：核对可调用性与任务运行上限，验证实际内容；已验证模型见[能力矩阵](MODEL_CAPABILITIES.md)，不外推全部目录。
- M05扩量/换模型：重新检查容量与签名、独立dev/test内容；当前两小合成集合不证明大规模或企业效果。
- M15：MySQL持久saver兼容、审批并发、下游幂等/查询与崩溃恢复。

MySQL保存业务事实，Redis/Milvus是可重建投影，checkpoint不是账本。门禁按当前特性启用，不阻塞纯离线工作。

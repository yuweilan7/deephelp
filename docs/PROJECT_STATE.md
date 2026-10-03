# 项目当前状态

更新：2026-10-03。只记录实现与实际验证；命令见 [ROADMAP](ROADMAP.md) / [LOCAL_SETUP](LOCAL_SETUP.md)。

| 范围 | 状态与证据 | 限制 |
|---|---|---|
| 工程 | Python3.14.7；根uv workspace、单一uv.lock；modules/deephelp-app | uv须满足根pyproject要求，本机启动器见LOCAL_SETUP |
| P00 | MySQL SELECT、Redis鉴权PING、Milvus元数据读取均PASS；报告 `.local/infra-health/client-health.json` | 只读连接验证，不是容量/业务检索/full复验；[历史交接](../handoffs/P00.md) |
| 学习环境权限/API | LIVE_VERIFIED；[权限及API交接](../handoffs/P00.md#学习环境权限与api调整) | 三个中间件应用账号全权限；Redis实际重启后复验、模型六项内容验证；未跑P00 full |
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
| M10 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M10.md) | 显式多问题续接、生命周期、MySQL事实/outbox、Redis/Milvus可重建；自动归属留M11 |
| M11 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M11.md) | 独立自动归属/更正/澄清、事件图及MySQL/outbox；完整主流程接入留M12 |
| M12 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M12.md) | 同一13段主链自动归属、四层级联及逐请求计数；校准/业务均为小合成组 |
| M13 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M13.md) | Windows真实FastText训练/量化及原600可选兜底；80条小合成组，默认关闭 |
| M14 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M14.md) | 版本注册表/静态校验/固定快照、三流程19合成情境；批准后模拟变更由M15接通 |
| M15 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M15.md) | MySQL持久checkpoint、明确审批/唯一领取/UNKNOWN查询对账；8进程/通信故障矩阵及真实模型HTTP合成效果通过，非企业支付/通用RUNNING重领 |
| M16 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M16.md) | 事实模板/受限语气、同源三视图与脱敏轮转；M15已接审批/恢复历史 |
| M17 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M17.md) | 四组对照及M15统一审批评测已实现；17操作/6越权/9恢复重放真实通过；500+与完整15+规模仍待扩展 |
| M18 | IMPLEMENTED_LIVE_VERIFIED；[交接](../handoffs/M18.md) | 审核回流与完整ReleaseManifest/MySQL单一active已实现；运行绑定、审批资产保留、并发回退及进程中断真实通过；不提供每日自动训练/隐式文件删除 |
| M19 | HARDENING_NOT_IMPLEMENTED | 部署/容量/备份恢复加固尚未实现 |
| M20、M21 | OPTIONAL_NOT_IMPLEMENTED | 不阻塞客诉核心路线 |
| 模型/业务联调 | MVP_LIVE_VERIFIED；企业业务 NOT_RUN | 通用Chat改qwen3.8-max，schema/tool及M18真实内容通过；原Embedding签名保持；Rerank三候选补验通过、主链未接入 |

M01已有客户端生命周期、deadline/预算/取消、错误/trace、fake Ports和三个异步实验。M02已原位扩展唯一类型，增加实体来源/更正、意图候选/策略、事实/工具证据、归属/参数/预算守卫和状态映射；目录有三类 actionable 意图，合成样本 reference/dev/regression=6/12/18，业务 fixture 有13订单、4券、1活动。缺订单演示为 CLARIFY/WAITING_SLOT/run SUCCEEDED/工具0。M03增加ChatPort/EmbeddingPort、严格schema/原生工具字段、调用/token/费用预留、错误分类、有界LRU和结构诊断；全零Embedding index按选定模型显式适配，normalization=l2、revision=null。未安装LangGraph；默认M01离线入口保持501，M08显式组装真实模型与MySQL三表。

M04新增兼容 DTO TextCleanResult / ExtractedEntity / RuleMatch / TextEntityResult。清洗只规范全半角ASCII及空白，原文、否定、金额/日期/数量、表情和尾部实体保留；Regex扫描完整原文，模型只补缺失/冲突字段。提取值必须匹配原文跨度、字段语义和白名单；多值不选最后一项，明确更正保留旧/新消息来源，模型不能覆盖已确认值。模型分段未完整覆盖或后段失败时，保留观察并澄清，不确认局部结果。三类版本化规则只提供候选；demand_type只描述话语功能。22条合成黄金样本及强模型端口内容验收通过，真实接口使用qwen3.8-flash/qwen3.8-max；不部署本地7B，不宣称原文推理时延。API未知/冲突保持未解析，预算/timeout/取消有界；M08复用该实体端口。

M05新增CorpusRecord/DenseScope/DenseHit/DenseCandidate/DenseResult及DenseRetrieverPort，版本集合隔离、幂等小批导入、未知提交续跑、逐条FP32向量hash与完整回读、本机版本切换/回退。只有reference/train入库，默认6条reference、12条独立dev：文档Recall@1=11/12、Recall@3=12/12，候选Recall@1=11/12、Recall@2=12/12；M02-028首位错误保留，未设最终接管阈值。合成dev不是未见test；qwen3.7-text-embedding-flash签名revision仍null。PyMilvus2.6.17加入应用及根锁。

M06新增正式mcp2.2.0依赖、两个M02命名只读工具、ToolPort/ToolGateway、受控stdio服务和独立smoke。身份/参数/版本/调用ID通过HMAC内部信封绑定，服务端再次检查订单/券归属；模型参数无身份/签名/故障开关，写工具拒绝。共享预算、排队deadline、单工具timeout、64KiB返回体与有界ledger；坏结构/矛盾不发布事实，注入文字只作数据。真实协议下金额/券状态/证据、双用户同进程并发、跨用户/租户、无签名/篡改/重放、故障、取消后会话复用和退出均验证。ledger为合成调用记录，管理resource需签名；不是副作用账本。M08已用固定合成身份Bearer入口接入converse；企业登录鉴权未实现。

M07新增三份JSON SOP/schema、版本化Prompt、SOPExecutorPort/受控执行单元和固定模型回放。已分类Question和可信context先做归属/版本/状态检查，缺失或冲突槽位WAITING_SLOT、模型/工具0；模型选择原生获准动作，观察后固定finish_sop，代码核对参数、对象、证据hash、分支和结束条件。优惠未到账无法确认原因时转人工，券可用只确认查询状态。循环/总时限/模型及工具子时限/单次与总重试有界，复用原预算；只重试明确限流/下游不可用只读失败，外部取消传播。ToolRequest可选timeout_seconds、运行时on_dispatch和失败调用ID保留兼容回归，成功证据只能指向实际调用；无新依赖/迁移。qwen3.8-flash+正式SDK stdio的9条内容路径、注入、失败及ledger/退出通过。空活动分支曾返回文字，已收紧原生结束动作并全量复验；原始诊断/预算留.local/m07。执行器不重新分类/写问题状态；主会话与状态落账由M08接入，LangGraph未启用。

M08新增单条完整问题Conversation及唯一600 IntentService、MySQL消息/run/question账本、本机Bearer入口、CLI/单页调试和完整验收。500显式passthrough；规则单类选择，否则Dense+一次schema确认；无未经校准接管阈值。缺槽位CLARIFY/WAITING_SLOT/run SUCCEEDED、工具0；未知/无SOP正常转人工，1100事实模板不输出原始模型/工具指令。接收唯一键、payload冲突、终结CAS、取消落账、发送ID、8并发唯一接收/跨用户隔离、新连接池RUNNING不抢占和应用资源重建后回放通过；终态提交失败不返回已解决。新增aiomysql0.3.2/PyMySQL1.2.3；Python/根workspace/单锁/infra保留。

M08真实HTTP十条内容路径通过（21模型尝试、9工具调用），12条原始固定样本真实验证意图/参数12/12，旧outcome11/12；M02-001无依据确认未到账原因而转人工。完整36条离线回放只是串联验证：标签36/36、参数24/36、旧outcome20/36，8 ANSWERED/18 CLARIFY/7 HANDOFF/3 ERROR；16差异保留，12个字段语义不足澄清、3个下游归属拒绝、1个M07保守转人工，不是全样本模型质量/业务成功率。原始报告在.local/m08。M08验收当时跨消息补槽/事件合并、Redis投影/记忆、完整级联、LangGraph、回复模型润色、审批/写工具和企业下游未启用；RUNNING硬中断不自动恢复。未跑P00 full或云服务/主机重启，资源重建不冒充这些验证。

M16特性分支全工程无外部网络回归：Ruff检查/格式、mypy退出0；Python3.14.7下 **755 passed、1 skipped**；既有pytest-asyncio policy API弃用警告仍在，Jieba依赖有旧正则SyntaxWarning。M08新增63项、M09新增34项用例，网关参数扩展新增4项、M10新增26项、M11新增36项、M12新增42项，M13新增39项、M14新增53项，M16新增37项专项回归（全工程755项后另补网关schema拒绝一例），原M01–M07消费者保持通过。integration包含合成ASGI/loopback HTTP及正式SDK本机stdio子进程，e2e验证组装/退出，默认pytest不代表云端业务e2e，M08显式probe另验真实云服务/合成业务。M02–M07无业务库迁移；M08增加专用三表且实际验证唯一约束；审批等待类型/写工具禁用。CI配置存在，本机结果不冒充远端CI执行证据。

LOCAL_SETUP指向本机凭据、模型摘要和连接结果；调用前实时核对可调用性。Windows入口统一py -3.14 -m uv，解释器3.14.7、uv0.12.13；根workspace和单一锁保留。模型选用遵循[AGENTS](../AGENTS.md)的学习效果优先原则，允许使用候选池和账号余额；当前网关仍为固定模型配置，自动切换未实现，默认不开启live。请求大小与输出按任务配置，推理支持按配置/请求开启；详情见[模型矩阵](MODEL_CAPABILITIES.md)，业务质量对照未执行。

M04/M05实际探针、累计运行上限及原始报告分别留.local/m04、.local/m05；默认演示离线。M05两个专用集合实际删除/恢复、过滤、版本切换/回退与服务重启持久性均通过。重启暴露Milvus内嵌etcd选举启动故障，冷备后复用原元数据改为同机独立etcd3.5.23，健康后启动Milvus2.6.23；两种真实重启及全量记录/向量hash与查询通过，MySQL/Redis未重启。云端cap总6016MiB/3.75CPU，etcd无主机端口；未验整机重启/高可用/大规模。机制和回退见ADR021/OPERATIONS，维护授权已按用户要求写入AGENTS。M06协议报告留.local/m06，只启动本机合成服务；本轮未调用模型、业务库或P00 full。

原始P00报告、旧安装提示、一次性生成工具和上游参考副本已移出当前工作树，保存在 `.local/archive/context-cleanup-20261002/` 和Git历史。运行配置、运维/验收工具、原PDF、M01源码/测试和模块规格保留；本次未启动下一M。

M09复用M05导入/manifest/容量门禁及唯一DTO，新增独立HybridScope与HybridRetrieverPort。真实Milvus2.6.23/PyMilvus2.6.17执行Jieba中文Analyzer、BM25 Function、SPARSE_INVERTED_INDEX及WeightedRanker；导入/查询分词同版本，保留不/未/not与前导零订单，SKU-A7按sku/a7拆分。新集合不修改旧Dense集合；原始cosine、BM25、融合分数/来源/rank进入600检索证据，不作为正确概率或接管阈值。M08显式传M09指针后接入同一主流程，旧M05指针兼容；默认仍指向M05，未自动推广最佳检索方案。

M09固定12条reference入库、18条dev选择三档权重，选中Dense/BM25=0.5/0.5；24条test不参与选参。test的18条单类查询：Dense/BM25/Hybrid Recall@1=16/18、18/18、17/18，Recall@3均18/18，候选MRR=0.9444/1/0.9722；24条含无关/多诉求的后续600分类为22/24、23/24、22/24。最终Hybrid比Dense多命中一例，仍退步一条券口语查询，多诉求分类错例保留；小合成组不证明企业效果或融合必胜。真实删除后检索缺失、原FP32哈希恢复、版本切换/回退、错误scope/signature过滤与旧M05全量回读通过；真实HTTP十条路径通过。LF重索引后保留原策略，真实Embedding向量哈希与Dense排序复验有波动；最终指标来自当前集合复验。原始对照/资源/诊断留.local/m09，M09验收未运行P00 full、历史/事件集合检索或服务重启。


M10新增CaseRepository/MemoryPort、兼容DTO、MySQL增量成员/session/outbox三表和redis6.4.0依赖。显式hint可补槽、冲突保留确认值并禁止工具、更正链保留来源，关闭后新消息新建；有证据的状态操作和显式重开使用CAS。只有600绑定/保持主意图，不自动归属交错消息。Redis窗口/关键词按generation写TTL缓存，事实读取始终来自MySQL；Milvus事件按question/version保存真实向量，命中再核验当前归属/状态/version。worker短事务领取、外部投影后token确认，失败有限重试；远程调用不持业务锁，集合删除后可按最新MySQL问题重建。

特性分支真实HTTP补充/冲突/更正/查询事实、8并发唯一接收、6并发CAS、乱序拒绝、Redis断连后重试、重复/逆序outbox、实际stale ACTIVE命中回查、关闭/重开、35条历史裁剪、专用缓存删除/恢复、新进程回读、lease过期与并发事实修改通过；原始报告`.local/m10/feature-final.json`。独立CLI按同scope重建并查询已结摘要内容通过。MCP协议真实，业务合成；该M10验收不含自动归属、审批、业务写和RUNNING重领。未运行P00 full或M10云服务重启；前置权限修正的Redis重启单独记录。

M11独立聚合入口复用M04/M10事实和派生事件集合。窗口、TopK、一次结构化裁决、摘要及共享deadline/预算有界；实体/状态/version/scope由代码复查，高把握与两侧精确引用才关联。整簇订单/问题锚点守卫防传递误并，明确A改B保留旧/新来源而非等价union。qwen3.8-max负责归属，Embedding沿用原1024维签名；未调用主意图分类器或SOP/业务工具。MySQL保存成员、可回放图及outbox，摘要保留最近当前消息；待澄清消息独立保存、排除自动候选，明确hint可补充。多个明确主诉为虚拟上下文并要求分条，不伪称已建多个持久问题。

特性最终真实8序列/19消息归属19/19、错误合并0、错误事件数0、澄清3；143项内容检查全通过，包含WAITING_SLOT自动归属、实际陈旧向量回查排除、独立集合删除/从MySQL重建、新连接池事件图回读、attribution CAS回滚、三维事务隔离及6并发重复唯一接收。原始结果`.local/m11/feature-verified.json`；此前flash矛盾更正输出安全澄清，收紧字段一致性后选强模型复验。效果只代表小合成组，未证明企业效果或完整多诉求切分。M11验收时主converse为M10显式hint；M12现已接入完整500→600及只读业务主链；未跑P00 full、云重启、审批/写工具或RUNNING崩溃重领。

M12在同一Conversation中接通300当前实体、400事实窗口、500自动归属、600四层级联及后续SOP/终态。已绑定事件保留主意图，未绑定事件只用当前已授权聚合及确认实体增强查询。配置绑定M09 scope/语料/权重，当前fusion阈值/差距0.85/0.05、记忆fusion0.85/0.3；18条dev及18条派生增强query分别接管5/18、6/18，错误接管0，未用M09 test选参。Dense/BM25未校准禁分数接管，旧M05指针兼容，FastText disabled。

真实HTTP15消息/168项检查全通过，规则、当前Hybrid、记忆增强Hybrid和schema兜底在同一主链实际命中；12次主分类、30模型尝试、5次600检索、8个实际工具调用ID。自动交错补槽/更正、歧义/多诉求/未知、缺槽位工具0、当前高分后下游对象归属拒绝、事实/证据、MySQL新池回读、终态重放零调用、MCP退出及专用事件集合清理通过。原始报告`.local/m12/feature-layers-verified.json`。旧MVP十条实际HTTP路径、下游失败/注入/过期券/空活动、MySQL并发唯一接收及新资源回放通过，报告`.local/m12/mvp-feature-strengthened.json`；不是独立企业质量测试。

兜底在main复验出现应减未减的语义改写拒识，已用完整语义定义和现有强模型qwen3.8-max修复，`m12-fallback-v2`、实际兜底模型写入版本。原失败句及改写/退款/金额询问/多诉求等8条真实分类内容全部通过；原失败及诊断保留.local，提取/Embedding/SOP沿原provider配置。

真实复验发现归属模型会误接补充或对唯一相容事件拒识，已增加绑定槽位相容性和明确补槽的确定性关联，再通过整簇约束/事务CAS；一般语义与更正仍由受控模型判断。两种泛化“请查一下/请核查订单”短语不再虚构第二主诉。每请求call_counts与内部stage计数分开，服务故障/取消/澄清仍维护终态；不安全和无SOP禁止执行。M12保留Python/workspace/单锁/原infra，无新依赖或迁移。其验收未跑P00 full、云重启、FastText、审批/写工具、RUNNING崩溃重领或企业接口；FastText可选兜底见下文M13。

M13使用fasttext-community0.11.8与Jieba0.42.1，在原Windows/Python3.14.7训练、保存/回读及量化；根单锁增加上述依赖及tqdm，原依赖版本保持，无库迁移/新Embedding空间或GPU环境。绑定单线程探针出现NaN，当前仅验证10线程/dim20/seed42并检查全部矩阵有限；不承诺多线程逐字节复现。共享预处理含全半角/空白、HMM关闭及基础/业务词典hash；不丢前导零或否定。

80条公开人工合成句按来源/改写组及规范化文本隔离，train/dev/test=40/20/20。两候选仅dev选参，原版/量化独立门限0.65及margin0.35/0.5；test均top1=16/20，train均40/40，dev均17/20。原版339877bytes、量化30910bytes；test安全接管10/20与7/20，错误0。三种同组真实FallbackPort关闭/原版/量化最终code及原因均20/20正确，强模型调用20/10/13；错误及原始报告保留.local/m13，不声称企业泛化或500+规模。

FastTextFallback只包装M12既有600端口，默认关闭，显式指针启用。top1、原概率、confidence_kind、review/actionable/unknown及三个版本一致，非法标签/损坏/hash/预处理不匹配不会发布。空/编号/OOV/低分/多诉求/未知沿同预算下探；命名意图但缺槽位仍CLARIFY/工具0。三模式21条真实HTTP消息/173检查全部通过，含实际接管、补编号沿同question查询事实、三类金额/券/活动证据、MySQL终态/零调用重放及MCP退出，专用事件集合已删除。activate内容回读后原子替换active/previous，重复发布/失败不丢上一版，rollback真实通过；模型本机保存，未上传云端。未跑P00 full、云重启、审批/写工具、RUNNING重领或企业接口。

M14在原执行器及M12主链增加三份有向无环配置、版本注册表、启动/发布静态校验、代码固定工具授权和失败终点。运行固定注册表/SOP/Prompt/工具签名与内容快照；发布回读后原子切换，旧问题补槽按保留快照继续，运行对象不被改写。旧M07问题/自定义配置及消费者兼容，无新依赖或迁移。券门槛分支真实查询两来源并按固定合成口径比对，失败不沿成功路径，矛盾人工；响应显示实际节点路径。

19条真实模型+正式SDK stdio情境全通过，包含8条业务分支及缺资料/归属/查无记录/注入/失败/矛盾/审批前计划；7条实际HTTP业务消息及51项内容检查通过。MySQL回读、终态重放零调用、发布后旧问题原版/新问题新版、计划绑定、回退及专用事件集合清理/MCP退出已验证，原始报告.local/m14。19条是三类意图的合成流程/边界情境，不是19类企业场景或未见质量。NEEDS_APPROVAL只产生sop_plan，HTTP仍HANDOFF/HANDED_OFF，未申请/批准/执行变更；M15必须重新核验当前问题版本、期限与操作领取。未跑P00 full、云重启、企业写接口、M15恢复或RUNNING领取，该轮未含M16验收，M16当前证据见下文。


M16沿原13段1100使用事实模板，可选真实模型只选固定非事实措辞，不接收金额/编号；新增事实/自由正文/坏结构/超时回退，取消传播。reply_presentation保留模板、方式/理由及版本，M14计划明确未申请/批准/执行。MySQL终态后生成一份脱敏debug_snapshot，意图/多轮/流程三个鉴权API/CLI/HTML投影同一run/原trace；原/清洗输入、规则/召回/级联、窗口/候选排除/归属/更正、输入question.version/最终版本、实际SOP及逐节点起止/下一步/错误、工具参数/hash/child timeout/dispatch/证据、预算与回复可查。outbox排队不冒充投影完成。

真实7条HTTP消息/87项检查通过，包含三类99.90/10.00、券usable、活动事实、四次实际受限润色、缺槽位/自动补充、未知、正式SDK工具超时、三视图节点/版本/实际调用、鉴权/导出、同消息零调用重放、新追踪资源及MySQL新池回读、专用事件集合删除/MCP退出；原始报告.local/m16/feature-final.json。37项专项离线测试覆盖恶意数字/动作/证据/正文、网关schema拒绝/失败/取消、并发观察隔离、三域权限、错误定位、轮转/磁盘故障/过期不丢账本、控制路径及HTML安全；实际浏览器三视图/导出正常，error日志为空。默认详细trace为128项非阻塞队列、4×2MiB/7天、单快照128KiB省略标注；稳定替代ID/key保留关联，span偏移指原文，凭据遮盖/导出公式防护。无依赖/锁/迁移变化；真实服务与合成业务，不是企业效果。未跑P00 full、云重启、M15批准/恢复/写业务或RUNNING领取；不启动M17。

M17新增冻结评测器、manifest和评测专用装配，原13段/唯一DTO/M09同语料及K/M12 dev门限/M13产物复用，无依赖/锁/迁移或默认业务配置变化。16合成案例20消息，test/regression=12案例16消息/4案例4消息；编号中立规范化为15不同序列，声明16来源/变体组，同作者语义独立性不能由程序证明。标签/实体出处与business.json固定hash由确定性规则核验，未把原索引/训练/dev当新test，未依test改生产参数。

离线四组全量使用固定语义/SOP适配器及真实本机HTTP/SDK stdio，仅验证编排/指标/门禁；保留continuation错拆分，不冒充模型质量或全场景完成。真实固定8案例11消息×4组通过，意图各11/11、已出现类macro-F1=1、实体值11/11/非空来源6/6，各工具6次；三类金额/券/活动事实、成功ledger证据、缺槽位/跨用户对象、新MySQL池/终态重放/集合清理/退出均通过。Dense/Hybrid整会话7/8，Memory/EventCluster/FastText 8/8，启用事件组误并0/4对、误拆0/2对；25/23/15/15真实模型调用与45/57/41/41共享预算尝试分别报告。FastText两次CPU预测未接管，此抽样没有额外调用收益；其他输入不能外推。

M17报告同配置/数据/代码/模型指纹，分split混淆矩阵/错例、Recall@K、实体来源、成对事件、澄清/接管、工具行为、消息/会话完成、P50/P95与估算费用；空分母null。main曾因无事件组续接的随机拒识不通过基线，现按冻结expectation/启用能力修复比较，原错例及全部硬门禁保留。修复特性/main真实四组均PASS，main原始完成10/11、10/11、11/11、11/11，实际比较9/9/11/11条；两个启用事件组续接严格通过。778 passed/1 skipped、Ruff/格式/mypy/diff退出0，修复已合并并同步远端。当前真实抽样不含multi类，未运行全部20消息真实模型、500+或完整15+业务规模；main证据见M17交接，原件.local/m17。

M18新增脱敏候选/独立路线审核、MySQL修订历史与CAS/短租约任务、三路构建与派生文件来源。高分/成功不自动晋级；固定审核证据只接受登记的合成来源，拒绝/争议/撤回保留。已知训练/索引/dev/test/M17、编号变体和来源/近义组不能回流；FastText只扩train，规则为有限字面数据。只读查询结束池连接快照，来源更正/撤回读取最新事实。去掉M05全项目固定4集合数量限制，仍按实际容量/预算/超时停止。

真实专用采集4条，三条获准、一条物流拒绝；9个构建任务各一次DONE。原12语料/80训练输入更新为15/83及3规则，真实1024维Embedding/Milvus全量hash回读、FastText训练/量化。基线和审核版分别固定M17的11条消息及另3条发布输入，均14/14、11/11会话，事实/实体来源/工具证据/重放/新池及事件关系通过。显式启用/幂等/回退后更正撤回一条来源，14份派生文件可查，旧审核版不能重启用；演示指针最终回到完整基线，默认M09指针保持原资产。25项离线边界测试通过；同版本只读词典复用/新版本隔离保持输出与签名，特性全工程804 passed/1 skipped及真实内容验收通过，原件.local/m18/feature-resume-verified。已合并main；main剩余回归与真实复验按用户最新要求跳过，具体退出状态见交接。真实服务与合成业务，未证明质量增益或企业泛化；完整ReleaseManifest/单一事实源并发发布、M15写业务、500+规模仍未做。

## 后续真实门禁

M15使用LangGraph1.2.12/Checkpoint4.2.0及既有aiomysql自建MySQL saver。独立进程interrupt→恢复→完成重放兼容门禁通过；M14计划在显式装配下进入原run的持久等待，当前主体、问题版本、参数、SOP快照、审批/期限与唯一dispatch由MySQL短事务保证。普通“好的”不批准，待审批更正拒绝；拒绝/撤销/过期结束未执行计划，已发送操作不能伪称撤销。UNKNOWN/IN_FLIGHT先查询合成下游，明确无效果才允许有界再次发送。独立下游效果/网络调用跨重启持久，checkpoint缺失/滞后按业务账本修复。

特性真实模型HTTP创建计划/批准/恢复/事实/原receipt终态与重复resume通过；删除专用Redis缓存和Milvus投影、实际outbox滞后不影响MySQL审批。8个应用/下游实际进程与通信故障情境均最终效果1；7例execute1，明确无效果重试例execute2；拒绝/撤销/过期、并发批准/恢复、四维隔离全通过。SOP切版后仍可按原绑定拒绝/撤销未执行计划或结束过期计划，新批准/dispatch仍核对当前SOP。M16流程/多轮视图新增MySQL审批历史、dispatch/query计数与最终事实。原件.local/m15，网络/云服务真实、模型主链单列、多故障固定动作仅证明恢复协议；非企业接口或完整模型质量。默认只读装配仍原行为，只显式合成权益服务可写；未启用任意13段RUNNING重领。

### M02–M18 的 M15 依赖核对

| 范围 | 本次核对结果 |
|---|---|
| M02–M13 | 已有核心实现；预留DTO/只读工具/记忆边界已兼容M15，没有新发现的阻塞特性 |
| M14 | 审批后合成变更由M15接通并真实验证；19情境属于三类意图机制覆盖，不能当19种企业主诉 |
| M16 | 审批/恢复信息已增量接入原调试视图，重复resume/当前账本状态真实验证 |
| M17 | 统一报告已接17个冻结操作、6个越权尝试及9个恢复重放；效果/发送/查询账本逐例核验，真实通过。500+与完整15+业务规模仍为下一扩量特性 |
| M18 | 完整ReleaseManifest/MySQL单一active已接M15；运行/续接绑定、待审批/UNKNOWN保留、并发回退及实际提交前后进程中断通过 |

M18完整发布新增四张增量表，receipt事务同时绑定问题及run引用，短事务CAS维护active/previous/发布事件，并在提交内再核对审核来源。清单绑定完整语料/Embedding/FastText词典/规则/SOP/Prompt/两路模型配置/代码及对应成功评测。保留原本机演示入口；完整发布入口不读本机active作为发布事实源。

真实两套12记录1024维新向量资产及三个完整清单，最终两版各14/14消息、11/11会话，23项发布/审批/资源检查全通过，原件`.local/m18-release/feature-v3`。运行中发布仍输出原完整版本，新请求输出新版，旧问题补槽回到原版；active/previous之外的待审批/UNKNOWN引用也阻止退休。SOP切版禁止旧操作新批准/发送，UNKNOWN按原效果对账并重放：effects/execute/query=1/1/1。并发回退只有一个revision获胜；实际发布子进程提交前/后exit88分别保留完整旧/新版，新MySQL池读取同一active。动态MCP资源关闭跨任务错误已修复，先前失败原件保留，最终全部退出。retire仅标记无引用版本，保守保留全部run审计及共享工件，不自动删除文件。

用户已启动M02–M18已登记剩余项，顺序交付独立特性；M17审批评测及M18发布加固已补齐，M14/M17覆盖扩量接续实施。M19–M21不在本轮完成范围。验收只在特性分支，main合并后不复验。

- 模型调用：核对可调用性与任务运行上限，验证实际内容；已验证模型见[能力矩阵](MODEL_CAPABILITIES.md)，不外推全部目录。
- M05扩量/换模型：重新检查容量与签名、独立dev/test内容；当前两小合成集合不证明大规模或企业效果。
- M15后续升级：saver/下游协议或版本变化需重跑兼容与故障矩阵，企业写/补偿接口另需真实门禁。

MySQL保存业务事实，Redis/Milvus是可重建投影，checkpoint不是账本。门禁按当前特性启用，不阻塞纯离线工作。

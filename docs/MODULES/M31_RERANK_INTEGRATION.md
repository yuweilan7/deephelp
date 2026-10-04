# M31｜Reranker 接入与检索效果验证

**状态：** PLANNED；首特性 `PROBE_FIRST / READY`。用户2026-10-04明确追加，不依赖M25–M30迁移；每次只做一个特性。

**前置：** 已交付M03模型预算/适配、M05/M09召回、M12唯一600主分类、M17评测、M18发布绑定、M20 Retrieval MCP和M22检索学习页。见[计划](../../planning/M31/PLAN.md)。M28完成后可用Langfuse观察，本模块也须兼容现有OTel，不能为了观测等待整套迁移。

## 当前缺口与目标

当前网关只有Chat/Embedding，HybridRetriever把Milvus召回结果按意图取最佳证据；Retrieval MCP同样没有Rerank调用。`qwen3.7-text-rerank`虽已登记并完成历史三候选合成内容验证，仍只是可用候选。补一个脚本或注册模型不算接入完成。

目标是在真实会话检索及独立Retrieval MCP中实际调用该模型，让学习页解释重排前后文档/意图顺序、实际调用和效果。优先使用`qwen3.7-text-rerank`，不悄悄换成Chat、Embedding或其他模型。最终默认启用与适用范围由同条件内容对照决定，不预设重排一定更准。

## 模型适配与响应边界

在现有domain端口/DTO、httpx网关及AsyncCalls/ExecutionBudget上增量增加Rerank能力，不另建模型代理或并行预算器。该模型用专用文本排序endpoint及`model/input/parameters`请求体；从本机池中只读该候选的endpoint/凭据配置，不能由Chat base URL猜路径，也不能套用qwen3-rerank扁平格式。凭据、账号/余额快照和原始供应商响应不提交公共仓库。

输入是同一query和有序、不可变的候选文档列表。响应index必须为非bool整数、唯一且在范围内，score有限；按请求top_n验证数量/完整性和稳定并列顺序。用index回映本地原始doc_id/content/intent/source，不能信任供应商返回的替换文本、意图或新ID。分数单列rerank类型，不当概率或与cosine/BM25/fusion直接相加；不得无依据假定统一0..1阈值。

对当前“用户表述→意图例句”任务，在dev比较适合语义相似度的instruct与必要基线；官方默认是问答段落相关性，不能直接当意图相似度已合格。选定instruct、模型和排序/失败策略随检索profile/发布版本绑定，冻结后不靠测试集反复调参。

实际发送attempt、超时/取消、有限重试、模型调用及usage进入原共享预算；重排输入预留考虑query重复到每篇document的计量，实际usage与估算分开，缺失标未知。离线fake/页面浏览不调用真实模型。仅一个事实执行点记录一次usage，客户端和观测父级不得再计费。

## 召回、重排与正式主链

先召回较宽的有界候选，再对召回文档重排，最后按意图最佳证据归并并截取最终top_k。区分召回candidate_budget、Rerank文档数/top_n和返回top_k；不能仅重排原top3已归并的意图，也不能把某意图的多篇高分求和制造类别数量优势。只送当前授权reference语料的文本，不送gold标签/dev/test答案或无关业务明细。

保留原Dense/BM25/Hybrid结果和各路原始分数、融合来源/公式；新增Hybrid+Rerank结果携带独立重排分数、原始证据和策略版本。600仍是唯一主意图服务，重排不增加第二次主分类、绕过规则/记忆/兜底或直接决定SOP/业务写操作。Embedding与语料/signature未变时不要求无意义重建向量空间。

原检索接纳/级联阈值与证据override按各自分数类型复核；不能把新rerank score塞进旧fusion/cosine阈值。需要新的选择阈值时在dev校准并随策略发布，在独立test验证拒识/相邻意图反例；只改顺序时仍保留原证据分数和接纳机制。

RemoteRetriever与Retrieval MCP采用同一已发布排序策略。服务端在权威检索处重排一次，客户端不再次重排；新profile_id绑定模型、instruct、候选预算、失败策略和语料/signature。按需升级DTO/工具响应/schema、签名delegation/allowance、RetrievalAction/receipt及validate_results语义校验：原validator只识别三路分数/融合，不能只新增一个字段绕过校验。客户端不得提供任意model/endpoint/documents/collection；新旧profile不混用，重排关闭时原三路协议与结果保持可验。

不可用/超时是否返回原Hybrid由已发布策略显式决定，标记`not_run/failed/fallback`及原因，不能冒称重排成功。伪造归属/profile、越界/重复index、非有限score或内容映射不可信均拒收；不能以fallback掩盖安全/协议错误。空召回、规则命中、缓存复用、重复receipt应如实显示跳过，取消不吞掉、不重复下游业务效果。

## 学习页、观测与发布

扩展现有M22检索页，展示Dense/BM25/Hybrid/Hybrid+Rerank四路同query对照：文档和意图名次变化、原始分数与重排分数、模型/instruct/profile、实际调用/usage/耗时、未运行或失败原因。只在明确执行时调用；查看历史不重新调用。沿用受保护trace/run归属与脱敏，M28原生SDK接入后复用同一网关观测，不另建平行页面或重复generation。

M17新增明确的rerank能力/对照范围与报告字段，保存冻结集、候选集和配置指纹；关闭组不得调用该模型。M18正式运行及策略/网关字节变化必须形成新的完整ReleaseManifest并通过对应验收；旧M23或旧模型PASS不证明新重排发布。学习试验可先显式开启，不直接修改active或给旧发布动态塞模型。规则/FastText/Memory、工具权限和账本机制保留。

## 顺序特性、验收与停止

1. **专用端口/协议试验：** 先离线验证index/score/完整性、错误/重试/预算/取消；再选2–4条固定代表query和reference候选做真实排序读回，证明专用请求、映射、实际usage和内容。开发前检查该候选可调用性，live前据候选长度说明调用/token与含修复余量的共享上限；不重跑全部Chat/Embedding或完整业务。历史三候选报告仅作定位依据。
2. **同链接入与受控对照：** 冻结召回集合，同一query/同一次Embedding向量比较Hybrid与Hybrid+Rerank；在dev确定候选预算/instruct/失败策略，独立test只作验收。报告召回Recall@N与排序Hit@K/MRR；有分级gold才用nDCG。覆盖否定、券/订单相近词、表述变体、同意图多文档及原正确排序反例。重排不能找回未召回文档，不能把候选扩大收益混作重排收益；固定测试/业务结果与延迟/用量同时报告。
3. **远端/页面/正式发布：** 同一批准策略接入真实会话、Retrieval MCP及原学习页；对代表检索业务验证真实模型实际调用、来源/最终意图/回复事实和仅一次600主分类。离线验证签名/预算、关闭/故障/幂等/取消边界；实库/跨进程固定协议动作不反复调用模型。按影响通过发布门禁后才能声明主链已接入；固定小合成样本不证明企业泛化。

每项完成按AGENTS交付并停止，下一项由用户另启。未改善则保留可演示的显式对照能力，报告有效范围与退化样本，不默认全量启用或夸大收益；鉴权/网络/模型不可恢复或必要内容验收失败时停止推进/合并。handoff记录已接入/实际调用/验证范围，而非把候选池登记当成果。

依据：[官方文本排序API](https://help.aliyun.com/zh/model-studio/text-rerank-api)及[当前能力事实](../MODEL_CAPABILITIES.md)。本模块是用户学习扩展，不宣称PDF已有该模型/实现。

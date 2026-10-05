# 模型网关能力与签名

实现入口 [gateway.py](../modules/deephelp-app/src/deephelp_app/adapters/model_gateway/gateway.py)，配置模板 [providers.example.json](../modules/deephelp-app/providers.example.json)，命令见 [LOCAL_SETUP](LOCAL_SETUP.md)，网关及后续模型切换验收分别见 [M03 交接](../handoffs/M03.md) / [M18 交接](../handoffs/M18.md)。本表描述已验证的接口，不外推全部千问模型。

| 模型 / 路径 | chat | 严格 schema | tool fields | Embedding | 适配 |
|---|---|---|---|---|---|
| qwen3.8-flash / chat/completions | 支持 | json_schema + 本地 Draft202012 校验；思考模式亦实测 | 原生 function 字段 | 不使用 | 推理可按配置/请求开启；工具字段完整时兼容 finish_reason=stop |
| qwen3.7-text-embedding-flash / embeddings | 不使用 | 不使用 | 不使用 | 1024 维有序列表 | 对此模型显式允许全零 index 时按返回位置映射；正常完整索引则排序；其他坏索引拒绝 |
| qwen3.8-max / chat/completions | M04及M18切换前实测通过 | M18严格schema/0007保真实测 | M18指定工具及参数实测通过 | 未验 | 通用对话与事件判断；enable_thinking=false，业务结果见M18交接 |
| qwen3.7-flash / chat/completions | 2026-10-05真实返回指定内容 | 严格json_schema及0007保真 | 指定工具与0007参数通过 | 不使用 | 当次通用/归属判断配置；历史业务切换证据见下文及M03交接 |

官方能力说明：[结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)、[Embedding 兼容接口](https://help.aliyun.com/zh/model-studio/embedding-interfaces-compatible-with-openai)、[模型信息](https://help.aliyun.com/zh/model-studio/qwen3-7-text-embedding-flash)。文档宣称支持不代替交接中的真实检查。

签名为 provider=qianwen、model=qwen3.7-text-embedding-flash、revision=null、dimension=1024、normalization=l2、text_normalization=nfc-strip-v1。revision 未由 API 提供，明确未知；日期不是伪造的模型修订号。当前指纹 `emb-sha256:a70c2435830cfe74268228dba115ed8bdbf49ff17f65529a2f9e58a98e849b5f`。网关显式做 L2 归一化；API 原向量模长接近 1 不等于客户端归一化策略。签名任一字段变化必须创建新集合/reindex 后切换，不复用旧缓存或混入已有 collection；同名 alias 的供应商内部漂移仍需后续版本治理，不能声称 API 向量等同 BGE-M3。

Embedding 文本只做 NFC 和两端 strip；重复输入按原位置恢复。按唯一输入分批（每批最多 20、单次默认最多 128（可配置）），LRU 最多 64 项及估算内存 4MiB，配置有更低上限；缓存只驻内存、结果复制、没有无限磁盘缓存。全零索引适配由此模型的独立批次/单输入对照证明，不扩展到未知模型。

默认只用一个网络重试层：httpx transport retries=0，AsyncCalls 受同一预算约束，可指数退避和有限抖动。live 专用入口 retry=0。JSON/schema 必须本地验证；显式 repair_once 最多修复一次且不重建预算。未知工具、截断 JSON、维度异常、NaN/Infinity、零向量和模型名变化返回 MODEL_OUTPUT_INVALID。网关解析工具调用但不执行业务工具。

配置单价用于保守预留，不是实际账单。预留包含输入字节数、协议余量和输出上限；合法usage结算，未知用量保留占用，突破运行上限停止新调用。免费额度用完、账号无法继续调用与429限流须区分；模型选择和恢复遵循AGENTS。当前网关按固定配置调用，不自动选择替代模型。

诊断 cassette 为 m03-structure-v1，只记录结构，不能回放原始语义输出；FakeGateway 使用明确的合成 responses fixture 验证协议。二者都不含秘密或完整敏感 prompt，也不代替真实能力验收。

## 业务效果验收前须核对的限制

选型原则见 [AGENTS](../AGENTS.md)。以下为当前接口约束；业务特性按任务检查并完成必要适配与验收。

| 已实现项 | 对业务效果的影响与验收要求 |
|---|---|
| ChatRequest默认输出2048 token，无本地4096上限；业务可显式覆盖 | 实际接受8192输出参数；最终长度受模型服务能力和任务预算影响。finish_reason=length仍拒收，不把截断当完整答案 |
| 移除Chat消息32条、工具声明16条的固定上限；请求/响应默认1MiB/16MiB，配置无固定上限 | 41条消息、76034字节正文实测；这不是模型最大上下文证明。可按任务调整字节限制，超限显式报错，未增加静默摘要/裁剪 |
| ProviderConfig和ChatRequest支持enable_thinking及thinking_budget | 请求覆盖配置；默认保留非思考模式兼容。已实测qwen3.8-flash思考+严格schema及用量；发送前将思考额度纳入原累计预算，只记录推理长度，不保存推理正文 |
| 通用Chat默认模板固定qwen3.8-flash；独立归属/强判模板仍qwen3.7-flash | 用户2026-10-05长期选择；本次三项接口与14消息内容证据见M03交接，当前安装与完整绑定只写PROJECT_STATE。历史配置不改写，不自动切换模型 |
| Embedding只做NFC/两端strip、相同文本+signature缓存和批次去重 | 没有摘要压缩或语义近似复用；原位置恢复、结果复制和缓存隔离已有测试 |

当前协议验收不证明模型、输入/输出限制或推理模式适合全部业务任务；业务质量仍须用目标样本验证。

## 可选模型目录

用户2026-10-03提供的当前可用候选如下。这里登记用途候选，不把账号开启/剩余额度视作接口实测；本机额度及到期日期保存在被Git忽略的`.local/model-pool-summary.md`和`.local/model-pool.json`。普通搜索若跳过忽略文件，会漏掉该目录。

| 用途 | 候选代码 |
|---|---|
| 默认通用模板 | qwen3.8-flash；用户2026-10-05明确长期选择，免费额度耗尽可使用余额；实际发布见PROJECT_STATE |
| 通用备选 | qwen3.8-flash、qwen3.7-flash、qwen3.7-flash-2026-07-15、qwen3.8-27b、kimi-k3、deepseek-v4-flash-0731、deepseek-v4.1-flash、glm-5.3 |
| 质量候选 | qwen3.8-max、qwen3.8-max-0902、deepseek-v4-pro-0813、qwen3.8-2.4t-a95b |
| Embedding | qwen3.7-text-embedding-flash、qwen3.7-text-embedding |
| Rerank | qwen3.7-text-rerank；M31专用RerankPort/httpx网关及三条query×12 reference真实内容通过；主链未接入 |
| 多模态/翻译候选 | qwen-mt-uni、qwen3.8-omni-flash、qwen3.8-omni-flash-realtime；本轮不调用 |

可用候选、端口已接入、目标能力实测和正式主链分别记录。更换Chat模型前验证任务所需schema/tool能力；更换Embedding按签名新集合/reindex。Rerank不能作为Embedding或Chat端口使用；新增可选ProviderConfig.rerank显式配置完整原生endpoint，使用同一AsyncCalls/ExecutionBudget和OTel观测。当前未启用自动切换。

2026-10-04 M31首特性端口/协议试验已交付，证据见[M31](../handoffs/M31.md)，运行参数见[应用入口](../modules/deephelp-app/README.md#m31-专用重排端口)。三条固定合成query使用同一12条reference文本，券不可用/优惠未兑现/含否定的活动查询首位均符合预期；无Chat或Embedding调用。该小样本只证明接口、index映射与实际usage。2026-10-05全部后续特性已验：同候选dev/test选择N6/default，真实会话/独立MCP/四路学习与开启/关闭完整发布通过；test总体持平且一条退化，正式默认关闭，开启版明确试用。不证明企业泛化或统一收益，范围见[M31规格](MODULES/M31_RERANK_INTEGRATION.md)。

2026-10-03按用户要求补验原Embedding及Rerank：两条Embedding均为1024维有限向量；`qwen3.7-text-rerank`真实接口对三条固定合成候选返回完整index/有限relevance_score，券无法抵扣查询首位是券有效期/使用条件候选。只证明该排序路径可调用与内容相符，不代表主链已启用、最终分类更准或企业效果。原始报告留`.local/model-interface-check/report.json`。请求结构参考[官方Rerank API](https://help.aliyun.com/zh/model-studio/text-rerank-api)的该模型专用input/parameters格式，不能与qwen3-rerank的扁平格式混用。

## qwen3.7-flash 历史切换

2026-10-05用户要求切换通用Chat及事件归属模型，两份公开模板均改为qwen3.7-flash。三项实际接口、11会话14消息正式内容以及云端PG订单/原生Langfuse/零调用重放通过；[M03交接](../handoffs/M03.md#qwen37-flash切换2026-10-05)记录首次拒识退化、修复与证据边界。[官方模型说明](https://help.aliyun.com/zh/model-studio/qwen3-7-flash)列出结构化输出和Function Calling，但本项目采用本次内容证据认定资格。

原默认提示在Flash初验中将物流误判成优惠。当前完整发布显式绑定[不可变Prompt模板](../modules/deephelp-app/intent-fallback.example.json)，先判断当前诉求是否属于支持范围，再参考召回样例；不把最相似样例当用户诉求。模板沿用M29的PromptArtifact和既有请求/schema契约，完整发布用release_cli的--prompt-artifact绑定；prepare和validate须传同一文件，不能只更换模型模板并套用旧PASS。未安装自动模型路由，未改推理默认值或缩减上下文/输出。

当前Embedding签名及Rerank策略保持。原M31开启版和历史审批继续绑定其原完整发布；它们未参加这次Flash切换，不得将旧清单改写为Flash。额度记录仍为本机旧快照，未查询当前账单或承诺剩余免费额度。

## qwen3.8-flash 长期默认

用户2026-10-05明确选择通用Chat固定qwen3.8-flash；默认模板与AGENTS一致，强判/Embedding/Rerank仍各自显式配置。当前版本的普通chat、严格schema及原生function调用分别实测；同一冻结11会话14消息内容及9项工具参数全部通过。结果只证明本次固定样本与配置，不将接口支持或一次延迟比较外推到所有业务；[M03交接](../handoffs/M03.md#qwen38-flash长期默认2026-10-05)保留证据范围，当前发布事实见PROJECT_STATE。

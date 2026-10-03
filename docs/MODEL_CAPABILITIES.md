# 模型网关能力与签名

实现入口 [gateway.py](../modules/deephelp-app/src/deephelp_app/gateway.py)，配置模板 [providers.example.json](../modules/deephelp-app/providers.example.json)，命令见 [LOCAL_SETUP](LOCAL_SETUP.md)，网关及后续模型切换验收分别见 [M03 交接](../handoffs/M03.md) / [M18 交接](../handoffs/M18.md)。本表描述已验证的接口，不外推全部千问模型。

| 模型 / 路径 | chat | 严格 schema | tool fields | Embedding | 适配 |
|---|---|---|---|---|---|
| qwen3.8-flash / chat/completions | 支持 | json_schema + 本地 Draft202012 校验；思考模式亦实测 | 原生 function 字段 | 不使用 | 推理可按配置/请求开启；工具字段完整时兼容 finish_reason=stop |
| qwen3.7-text-embedding-flash / embeddings | 不使用 | 不使用 | 不使用 | 1024 维有序列表 | 对此模型显式允许全零 index 时按返回位置映射；正常完整索引则排序；其他坏索引拒绝 |
| qwen3.8-max / chat/completions | M04及M18切换前实测通过 | M18严格schema/0007保真实测 | M18指定工具及参数实测通过 | 未验 | 通用对话与事件判断；enable_thinking=false，业务结果见M18交接 |

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
| 通用对话切换为qwen3.8-max；Embedding保持qwen3.7-text-embedding-flash | 用户因Flash额度不足授权切换；切换前chat/schema/tool/embed真实接口通过，Embedding签名保持兼容；业务回归见M18交接，不声称最优 |
| Embedding只做NFC/两端strip、相同文本+signature缓存和批次去重 | 没有摘要压缩或语义近似复用；原位置恢复、结果复制和缓存隔离已有测试 |

当前协议验收不证明模型、输入/输出限制或推理模式适合全部业务任务；业务质量仍须用目标样本验证。

## 可选模型目录

用户2026-10-03提供的当前可用候选如下。这里登记用途候选，不把账号开启/剩余额度视作接口实测；本机额度及到期日期保存在被Git忽略的`.local/model-pool-summary.md`和`.local/model-pool.json`。普通搜索若跳过忽略文件，会漏掉该目录。

| 用途 | 候选代码 |
|---|---|
| 当前通用模型 | qwen3.8-max；用户后续授权切换，已通过chat/schema/tool内容检查 |
| 通用备选 | qwen3.8-flash、qwen3.7-flash、qwen3.7-flash-2026-07-15、qwen3.8-27b、kimi-k3、deepseek-v4-flash-0731、deepseek-v4.1-flash、glm-5.3 |
| 质量候选 | qwen3.8-max、qwen3.8-max-0902、deepseek-v4-pro-0813、qwen3.8-2.4t-a95b |
| Embedding | qwen3.7-text-embedding-flash、qwen3.7-text-embedding |
| Rerank | qwen3.7-text-rerank；已登记本机候选池，真实三候选排序内容通过，当前主链未接入 |
| 多模态/翻译候选 | qwen-mt-uni、qwen3.8-omni-flash、qwen3.8-omni-flash-realtime；本轮不调用 |

可用候选、应用已接入、目标能力实测分别记录。更换Chat模型前验证任务所需schema/tool能力；更换Embedding按签名新集合/reindex。Rerank是检索排序候选，不能直接作为Embedding或Chat端口使用；接入需单独实现与验证。本轮候选更新不启用自动切换。

2026-10-03按用户要求补验原Embedding及Rerank：两条Embedding均为1024维有限向量；`qwen3.7-text-rerank`真实接口对三条固定合成候选返回完整index/有限relevance_score，券无法抵扣查询首位是券有效期/使用条件候选。只证明该排序路径可调用与内容相符，不代表主链已启用、最终分类更准或企业效果。原始报告留`.local/model-interface-check/report.json`。请求结构参考[官方Rerank API](https://help.aliyun.com/zh/model-studio/text-rerank-api)的该模型专用input/parameters格式，不能与qwen3-rerank的扁平格式混用。

# 模型网关能力与签名

实现入口 [gateway.py](../modules/deephelp-app/src/deephelp_app/gateway.py)，配置模板 [providers.example.json](../modules/deephelp-app/providers.example.json)，命令见 [LOCAL_SETUP](LOCAL_SETUP.md)，实际验收见 [M03 交接](../handoffs/M03.md)。本表描述本轮选定接口，不外推全部千问模型。

| 模型 / 路径 | chat | 严格 schema | tool fields | Embedding | 适配 |
|---|---|---|---|---|---|
| qwen3.8-flash / chat/completions | 支持 | json_schema + 本地 Draft202012 校验 | 原生 function 字段 | 不使用 | enable_thinking=false；工具字段完整时兼容 finish_reason=stop |
| qwen3.7-text-embedding-flash / embeddings | 不使用 | 不使用 | 不使用 | 1024 维有序列表 | 对此模型显式允许全零 index 时按返回位置映射；正常完整索引则排序；其他坏索引拒绝 |

官方能力说明：[结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)、[Embedding 兼容接口](https://help.aliyun.com/zh/model-studio/embedding-interfaces-compatible-with-openai)、[模型信息](https://help.aliyun.com/zh/model-studio/qwen3-7-text-embedding-flash)。文档宣称支持不代替交接中的真实检查。

签名为 provider=qianwen、model=qwen3.7-text-embedding-flash、revision=null、dimension=1024、normalization=l2、text_normalization=nfc-strip-v1。revision 未由 API 提供，明确未知；日期不是伪造的模型修订号。当前指纹 `emb-sha256:a70c2435830cfe74268228dba115ed8bdbf49ff17f65529a2f9e58a98e849b5f`。网关显式做 L2 归一化；API 原向量模长接近 1 不等于客户端归一化策略。签名任一字段变化必须创建新集合/reindex 后切换，不复用旧缓存或混入已有 collection；同名 alias 的供应商内部漂移仍需后续版本治理，不能声称 API 向量等同 BGE-M3。

Embedding 文本只做 NFC 和两端 strip；重复输入按原位置恢复。按唯一输入分批（每批最多 20、单次最多 128），LRU 最多 64 项及估算内存 4MiB，配置有更低上限；缓存只驻内存、结果复制、没有无限磁盘缓存。全零索引适配由此模型的独立批次/单输入对照证明，不扩展到未知模型。

默认只用一个网络重试层：httpx transport retries=0，AsyncCalls 受同一预算约束，可指数退避和有限抖动。live 专用入口 retry=0。JSON/schema 必须本地验证；显式 repair_once 最多修复一次且不重建预算。未知工具、截断 JSON、维度异常、NaN/Infinity、零向量和模型名变化返回 MODEL_OUTPUT_INVALID。网关解析工具调用但不执行业务工具。

配置中的输入/输出单价是保守的 ¥8/百万 token 上界，用于预算预留；它不是供应商报价或实际已付款金额。字节数加固定协议余量和 max_tokens 为预留估计，关闭 thinking/search，限定输入规模；真实 usage 若突破总上限即报错停止新调用。供应商计量不可由客户端强制中断，未知用量按预留占用，实际账单另查。余额/免费额度耗尽与 429 限流分开分类，不因存在免费额度开启 live。

诊断 cassette 为 m03-structure-v1，只记录结构，不能回放原始语义输出；FakeGateway 使用明确的合成 responses fixture 验证协议。二者都不含秘密或完整敏感 prompt，也不代替真实能力验收。

## 业务效果验收前须核对的限制

2026-10-02按代码及本机累计证据核对；本次未重新调用模型。选型与预算原则见 [AGENTS](../AGENTS.md)，本节记录已有实现的约束，不表示限制已放宽。

| 已实现项 | 对业务效果的影响与验收要求 |
|---|---|
| ChatRequest默认输出128 token，字段上限4096 token；complete()沿用默认值 | 短协议探针可用；业务显式设置足够输出长度，需要更长输出时先调整契约与预算并验证。网关拒绝finish_reason=length，不能将截断内容当完整答案 |
| Chat消息最多32条；请求体默认32768字节、配置上限65536字节 | 这是本地输入边界，不是模型上下文窗口。按实际历史、检索证据和工具结果检查容量；超限报错，没有自动保留业务关键内容的裁剪器，不能静默删事实来适配 |
| enable_thinking固定false，尚无任务级推理开关 | 只验证过当前模式的协议能力；复杂判断/回答是否需要推理，须在对应特性中对照并适配，不能仅为省token固定关闭 |
| 选定qwen3.8-flash与qwen3.7-text-embedding-flash | 已通过chat/schema/tool/embed接口验收；未比较业务判断、中文回答或语义检索效果，不能认定最优，也不能仅凭Flash名称认定质量不足 |
| Embedding只做NFC/两端strip、相同文本+signature缓存和批次去重 | 没有摘要压缩或语义近似复用；原位置恢复、结果复制和缓存隔离已有测试。保留此实现，新增费用优化按实际需求与效果证据决定 |

接口验收、诊断和合并复验的短文本token用量不代表业务回答长度，也不作为后续预算模板；累计记录见 [PROJECT_STATE](PROJECT_STATE.md)，历史证据见 [M03交接](../handoffs/M03.md)。后续特性先用固定目标样本验证正确性、完整性与可调试性，再决定是否增加费用优化。当前协议验收不证明模型能力、输入/输出限制或推理模式适合全部业务任务。

# DeepHelp 应用与离线契约

Python 3.14.7 异步骨架及 M02 `0.2.0-m02` 离线契约；启动/凭据见 [LOCAL_SETUP](../../docs/LOCAL_SETUP.md)，检查命令见 [ROADMAP](../../docs/ROADMAP.md)，验证证据见 [M01 handoff](../../handoffs/M01.md) / [M02 handoff](../../handoffs/M02.md)。真实 ModelGateway 属于 M03。

## API

- `GET /health`：200，`{"status":"ok","module":"M01","capability":"NOT_IMPLEMENTED"}`；只证明应用存活。
- `POST /converse`：接收 channel、session_id、message_id、raw_text、带时区 occurred_at；返回 501 / NOT_IMPLEMENTED，run/question 为 null，事实/证据为空、调用数 0。完整传输字段见 `domain/models.py`。不执行主分类、SOP、模型调用或业务记账。

入口只允许 loopback/testclient 的独立本地测试身份，不用于公网鉴权；正文拒绝自报身份和诊断 ID。UUID 格式的 X-Request-ID / X-Trace-ID 仅供关联，缺失/非法则生成新值，响应头和正文一致。非法传输、缺身份、总 deadline、意外异常分别映射 422/401/504/500，错误消息不回显输入或异常详情。

## 实现边界

| 文件 | 职责 |
|---|---|
| `app.py` / `settings.py` | ASGI 上下文、lifespan / 显式环境配置与 live 门禁 |
| `domain/models.py` / `domain/checks.py` / `errors.py` | 唯一契约、实体/意图/事实/证据、状态映射、归属/参数守卫和 HTTP 错误映射 |
| `domain/registry.py` / `samples.py` / `sample_data/` | 显式三类意图目录、36 条固定合成样本、订单/券/活动 fixture 与指标定义 |
| `execution.py` | 共享 deadline/预算、Semaphore、只读 HTTP seam |
| `ports.py` / `fakes.py` | ModelGateway/Repository Protocol / 合成替身 |
| `gateway.py` / `providers.py` / `model_fakes.py` | M03 ChatPort/EmbeddingPort 实现、显式配置、合成 fixture |
| `live_probe.py` | 独立受控真实验收；累计预算、脱敏结构与环境签名 |
| `trace.py` | 串行线程文件写、JSONL / 内存 trace |
| `text_entity.py` / `text_entity_probe.py` | M04文本保真、原文实体证据、规则候选 / 离线演示和受控内容验收 |
| `corpus.py` / `dense.py` / `milvus_dense.py` / `dense_cli.py` | M05语料校验、可续跑导入、Dense候选、Milvus及版本指针 / 内容验收 |
| `experiments.py` | 三个离线异步实验，说明见 [学习材料](../../docs/learning/M01_ASYNC_GUIDE.md) |

lifespan 通过 AsyncExitStack 管理共享 httpx 客户端，包括启动中途失败。每请求只创建一次 ExecutionBudget；排队、子调用与重试共用总 deadline，子 timeout 不延长它。只读且显式 retry_safe 的操作可有限重试；编程异常/取消继续传播，stream 在取消/错误时归还连接槽。默认连接 10、并发 2、deadline 5 秒、子 timeout 1 秒、调用 3 次、重试 1 次；均由设置校验。

trace 默认 `logs/m01-trace.jsonl`，仅记录诊断 ID、事件、长度和状态，不含正文、秘密或隐藏推理。FakeRepository 按 tenant/user/channel/message 隔离并深复制，同 payload 保留第一次关联结果，不同 payload 返回 IDEMPOTENCY_CONFLICT；不是持久账本。导入不建连接、读秘密或启动进程池。默认 fake 拒绝网络，不因 Key 存在切换 live。

## M02 离线演示

从仓库根执行：

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.samples
```

入口校验 36 条 gold 样本和业务 fixture 的引用/归属、显式目录及 source/variant split 隔离，并运行纯状态映射：缺订单 → CLARIFY / WAITING_SLOT / run SUCCEEDED / 工具 0。不会预测意图、执行 SOP 或调用模型/云库。JSON 示例与准确率、macro-F1、覆盖/错误接管、实体、工具和场景指标口径见 [合成数据说明](src/deephelp_app/sample_data/README.md)。

下游从 `deephelp_app.domain.models` 导入类型，按 `domain.checks` 校验归属、实体、工具上下文/结果，不复制 DTO。金额只接受 Decimal 或十进制字符串，订单/券/SKU 保留字符串前导零；未知模型/embedding/费用版本保持 null。预算摘要只表达当前剩余量，不能替代原运行时预算。审批等待枚举保留但当前类型拒收，业务写工具不在白名单。

## 验证范围

unit 验证类型、设置、预算和实验；integration 用真实本机 httpcore 池验证取消后连接槽复用；e2e 验证 ASGI 组装/Uvicorn 生命周期，均不代表云端业务效果。默认测试阻止外部 DNS/连接。实验可用 `py -3.14 -m uv run --locked python -m deephelp_app.experiments`；CI 依据峰值、事件、清理及 heartbeat，不设毫秒门槛。

`tests/live` 默认跳过。pytest 的 --live 仍只测配置门禁；真实网关验收使用独立的 M03 入口，见 [LOCAL_SETUP](../../docs/LOCAL_SETUP.md)。业务 converse 的 live 组装仍为 NOT_IMPLEMENTED，不因网关可用就进入尚未实现的 M08 主流程。GitHub Actions 使用根锁执行静态检查和离线 pytest。

## M03 模型网关

`providers.create_gateway(client, config, calls, env=...)` 复用调用者管理的 AsyncClient。transport retries 必须为 0；chat/embed 分别配置 endpoint/model/Key 字段，当前实现不自动切换模型。候选池及账号余额的使用遵循[AGENTS](../../AGENTS.md)，显式换配置后重新验收。调用前创建一次带 token_limit/cost_limit 的 ExecutionBudget 并在整个请求内复用。旧 complete(text, budget) 转为普通 ChatRequest；新调用者直接使用 ChatPort.chat / EmbeddingPort.embed。

ChatResult 包含 usage、finish_reason、provider_request_id、校验后的 schema 或 tool_calls。严格输出用 json_schema 并本地校验；显式 repair_once 最多一次修复，继续扣原预算。网关只解析工具协议，不执行 MCP 或业务写工具。Embedding 返回输入顺序的向量、provider/model/revision/dimension/normalization 签名、缓存计数与全零索引适配标记，规则和配置见 [模型矩阵](../../docs/MODEL_CAPABILITIES.md)。

请求先预留调用/token/费用上界，收到合法 usage 才结算预留。超时、取消或未知 usage 保留可能发生的计费占用，缓存命中不新增 API 费用。`gateway.record_diagnostics(path)` 写出最多 100 条结构 cassette，不含 prompt、正文、参数、凭据；`model_fakes.FakeGateway` 回放明确合成 fixture，费用为零。离线测试覆盖失败分类、重试上限、共享预算、一次修复、全零索引、向量校验和缓存隔离。

## M04文本与实体

从根运行不调用模型的演示：

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.text_entity_probe
```

演示校验20条无需API的合成黄金样本，并显示长文尾订单的原文跨度、分段数及分层计量。黄金集共22条，另2条带引号编号需要结构化API，真实验收入口见[LOCAL_SETUP](../../docs/LOCAL_SETUP.md)，证据见[M04交接](../../handoffs/M04.md)。这是固定回归集，不是未见评测或训练语料。

`TextEntityProcessor(primary=None, strong=None, policy=TextPolicy(), trace=None).process(request, budget, confirmed=(), fields=None)`使用原ExecutionBudget，清洗与抽取并行。默认Regex；显式注入ChatPort才启用缺失/冲突字段的结构化提取，可另注入强端口。TextPolicy配置清洗分段、模型分段/数量、子timeout及输出上限；范围经本地校验，没有隐式重试或新建预算。模型扫描选中的所有原文段；后段失败/覆盖上限必须澄清，不能拿前段命中确认整个长文。

TextEntityResult保留完整原文、清洗分段、观察、当前实体、冲突、未解析字段、话语功能和规则候选；跨度与枚举语义见[CONTRACTS](../../docs/CONTRACTS.md)。confirmed由调用者提供已授权的前置消息实体；M04不访问会话存储。下游必须检查unresolved_fields，即使保留的旧实体仍在entities中，也不能绕过澄清执行工具。规则带条件/排除项/优先级/证据/version；300不确定最终主意图，600再统一消费。converse业务入口仍待M08。

清洗只规范全半角ASCII及空白，不删否定、表情、日期/金额/数量或改拼写；Regex读取完整原文。模型值必须在原文且可核对字段语义，未知不猜、多值不最后写入获胜、较低可信结果不覆盖已确认值。trace新增300_TEXT_ENTITY事件，仅含诊断ID、长度、层次、数量/耗时/错误，不含正文或提示。

## M05语料与Dense基线

从根运行离线预览，模型/云库调用为0：

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.dense_cli preview
py -3.14 -m uv run --locked python -m deephelp_app.dense_cli preview --source $denseCorpusFile
py -3.14 -m uv run --locked python -m deephelp_app.dense_cli --help
```

输入为UTF-8 JSONL/带表头CSV；无表头XLSX只接受一个工作表，必须用`--columns`指定字段→A/B等列字母映射，字段保存为文本，拒绝公式且不计算缓存值。每文件最多2MiB/1000行，content最多2000字符；缺字段、空行、未知/父节点标签、层级不符、重复ID/规范化正文、跨split来源/近义组均拒绝整批。数据字典见[合成数据说明](src/deephelp_app/sample_data/README.md)。

默认从M02的`cases.json`适配30条非空意图标签记录：6条reference入库、12条dev评测、12条regression仅保留manifest；另6条未知/否定不作为有标签参考语料。来源/近义组独立，这些公开样本不能当未见test。真实命令见[LOCAL_SETUP](../../docs/LOCAL_SETUP.md)。

`DenseImporter(embeddings, store, capacity).import_corpus(preview, scope, budget, manifest)`复用同一预算与EmbeddingPort，默认8条串行批次。先容量检查再建立/校验集合；稳定doc_id upsert，逐批回读正文、标签、版本、metadata和FP32向量。manifest原子保存pending/completed及每条完整FP32向量的SHA256；未知写入结果按相同主键续跑，已完成批次仍须回读并核对哈希。已验证且本次无写入的重导不重复flush，避免服务器0.1/s限流。manifest锁阻止同文件并发；异常/取消释放锁，进程崩溃遗留锁须核对后处理。

DenseScope绑定namespace/dataset_version/完整EmbeddingSignature/registry版本；集合名从整个scope派生，description绑定scope及索引digest。FloatVector FP32、FLAT/COSINE；同维度不同模型不共用集合。每批检查P00容量门禁，最多保留4个M05集合；到上限安排维护，不能无限建集合绕过容量。

`DenseRetriever.retrieve(text, scope, budget, top_k=3)`实现DenseRetrieverPort，返回原始命中和`per_intent_max_v1`候选：每意图独立取最佳一条，再按cosine排序，同分按code/doc_id稳定排序；不求和样本分数。保留doc_id、code、raw_score、score_kind、metadata与版本；不产生最终IntentDecision或接管阈值。namespace是语料范围，不能替代用户鉴权。

`activate`只把回读完整且manifest VERIFIED的集合写入本机原子指针；`rollback`复验上一版本后切回，失败保留现指针。它不是业务账本或多主机发布系统。`verify/search/evaluate`不创建或修复集合，持久性复验不能用重新导入掩盖丢数据。维护删除只支持指定scope内已有synthetic记录及显式开关，恢复使用同语料的新manifest。BM25/Hybrid、最终意图识别与业务converse留后续模块。

# DeepHelp 应用与离线契约

Python3.14.7，唯一业务契约`0.2.0-m02`。三类客诉使用下方M08入口；启动/凭据见[LOCAL_SETUP](../../docs/LOCAL_SETUP.md)，检查命令见[ROADMAP](../../docs/ROADMAP.md)，实际证据见对应handoff。

## API

- M08 `GET /health`返回M08/READ_ONLY_SINGLE_MESSAGE，只证明应用存活；`POST /converse`接收channel/session_id/message_id/raw_text/带时区occurred_at，需要固定合成身份的Bearer令牌。业务结果包含run/question、事实/工具证据、13段耗时与调用数。
- 未传业务组装器的M01离线`create_app`保留501/NOT_IMPLEMENTED，run/question为null、调用0；不会因机器存在Key自动联网。

M01使用loopback/testclient本地测试身份，M08本机令牌映射为固定合成身份，不开发登录系统，服务仅绑定127.0.0.1。正文拒绝自报身份和诊断ID。X-Request-ID/X-Trace-ID仅供诊断，响应头/正文一致；回放沿用业务run/结果但使用本次传输ID。入口错误不回显输入或异常详情。

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
| `mcp_protocol.py` / `mcp_mock.py` / `tool_gateway.py` / `mcp_smoke.py` | M06只读注册表、真实stdio服务、可信工具网关 / 合成内容与故障验收 |
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

`tests/live`默认跳过，pytest的--live仍只测历史配置门禁。真实网关/业务闭环使用对应probe，M08入口见下方。GitHub Actions使用根锁执行静态检查和离线pytest。

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

## M06真实MCP只读工具

`get_order_benefits(order_id)`查询M02合成订单实付、优惠及活动；`check_coupon(order_id, coupon_id)`查询券状态、门槛和可用性。ID保持字符串和前导零。正常查询返回唯一ToolResult及实际call ID、事实、证据引用和内容hash；不存在、越权、坏参数或坏结果不发布成功事实。写工具不注册，refund_order等名称明确拒绝。

从仓库根运行，前两条只预览，不启动进程。`--live`启动正式SDK的本机stdio子进程，实际执行list_tools/call_tool；不调用模型/云库，也不运行P00。输出需使用新的.local文件名，重跑更换名字：

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.mcp_smoke
py -3.14 -m uv run --locked python -m deephelp_app.mcp_smoke --help
py -3.14 -m uv run --locked python -m deephelp_app.mcp_smoke --live --stage feature --output .local/m06/feature.json
py -3.14 -m uv run --locked python -m deephelp_app.mcp_smoke --live --stage main --output .local/m06/main.json
```

feature验正确金额/券状态/证据、双用户同进程并发、越权、不存在、模拟500/限流、缺字段/矛盾/体积、恶意文字、timeout和外部取消、取消后复用、ledger与退出；main最小复验发现、两工具内容、归属及退出。报告PASS还需退出码0。完整schema/身份篡改/签名重放/预算/启动失败/生命周期取消由`tests/integration/test_m06_stdio.py`的真实stdio回归验证，纯校验测试在`tests/unit/test_m06_contracts.py`。

下游通过`ToolPort.execute(request, question, budget, context=可信RequestEnvelope)`消费；`ToolGateway.open()`在服务生命周期中进入一次，多次调用复用一个Client/子进程。入口注入已验证context；网关核对Question的主体、session、槽位及状态，内部签名信封传给服务端再次检查订单/券归属。工具schema没有user_id、凭据、内部签名或故障开关。M06合成调用者不是登录认证；接入真实应用入口在M08实施。

默认启动限15秒、单工具5秒、并发4、返回体64KiB、ledger256次；每请求复用ExecutionBudget，排队占总deadline，调用前预留，重试0。smoke默认总120秒/48次，参数`--timeout`/`--max-calls`仅本轮上限。主动取消传播CancelledError；SDK发送取消并回收子进程，服务端相对deadline提供额外上限。

默认MockConfig无故障；feature加载[故障fixture](src/deephelp_app/sample_data/mcp-faults.json)。支持延迟、下游500/限流、缺字段、矛盾、注入文字和超大结果，都是合成业务故障。`gateway.ledger()`读取签名保护的管理resource，不是模型工具；记录实际主体/参数、request/trace/operation/call及证据ID。可选ledger_path将有界合成快照写入新文件；密钥/签名不写盘。服务端查询完成而网关拒绝损坏输出时，ledger保留实际查询成功，diagnostics记录MODEL_OUTPUT_INVALID。ledger不提供业务副作用持久性或幂等；业务写工具仍需M15。

## M07最小SOP执行

三份 [SOP配置](src/deephelp_app/sop_data/) 对应优惠未享受、券不可用、订单活动查询；[schema](src/deephelp_app/sop_data/schema.json) 与 [Prompt](src/deephelp_app/sop_data/sop-react-v1.txt) 均版本化。配置限定 `lookup → evaluate`，分支只比较事实字段的 `eq/empty/nonempty`，不执行表达式。优惠未到账时已有查询不能解释原因，返回事实并转人工；券/活动按证据返回状态，查询可用不承诺结算成功。

根目录运行下面的演示：固定模型动作回放、本机真实SDK stdio和合成业务数据，外部模型/云库调用为0。输出三份完整SOPResult和真实ledger。

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.sop_probe
py -3.14 -m uv run --locked python -m deephelp_app.sop_probe --help
```

`SOPExecutor(model: ChatPort, tools: ToolPort).execute(question, budget, context=可信RequestEnvelope, run_id=...)` 是独立受控执行单元。输入Question已确定意图、归属、实体来源和SOP版本；入口先鉴权/核对版本，缺槽位或未解决更正返回WAITING_SLOT，模型/工具0。完整Question须为ACTIVE；执行器不重新分类、不写问题状态，主流程用既有 `response_from_sop` 映射CLARIFY/WAITING_SLOT等状态。

模型每步只选一个已声明工具或 `finish_sop/handoff_sop` 控制信号；取得成功观察后，阶段固定原生finish_sop调用，避免模型改为文字回复。控制信号由本地代码处理，不是MCP工具。参数必须逐项等于已验证槽位，未知/写工具、换订单/附带身份和重复已完成查询均拒绝。用户/工具文字留在不可信消息中，SOP/白名单只来自本地配置。观察复用M06的关联、对象、类型、证据hash和金额/券状态检查，配置所需事实不足不得RESOLVED。结论由配置分支产生并引用实际工具证据，模型不填写事实/状态。

每份配置包含max_steps、max_tool_calls、总时限、模型/工具子时限、单次与总重试上限；所有模型/工具共享原ExecutionBudget，参数不能放大调用者预算。只对明确的限流/下游不可用只读失败有限重试，timeout/取消/鉴权/坏结果不重试。ToolRequest可用 `timeout_seconds` 收紧网关子时限；ToolPort可用运行时 `on_dispatch(call_id)` 在发送await前记录ID，总deadline收口仍可对账。失败调用ID保留，失败没有事实；ledger是合成查询审计，不是持久副作用账本。

真实模型+真实MCP内容验收、累计预算初始化和参数见 [LOCAL_SETUP](../../docs/LOCAL_SETUP.md#m07真实模型与sop验收)。默认pytest无外部网络，覆盖三个SOP正常/缺资料/失败、循环、注入、版本、证据、共享预算、重试、timeout和取消；真实stdio与固定回放组合属于本机集成。主会话复用该执行端口，润色/复杂SOP治理/审批留对应后续模块。


## M08单条完整问题闭环

输入示例：`订单 000031 未享受优惠，请查一下`、`订单 000042 的券 000009 不能用`、`查询订单 000053 参加的活动`。模型/Milvus为真实服务，MCP为正式SDK本机stdio，订单/券/活动为合成数据。输出核对事实模板和工具证据，不以模型文字宣称成功。

从仓库根启动。首次创建新的令牌与累计预算文件，已有文件直接复用、不清零。Key显式加载`.env.local`，数据库复用infra配置/秘密文件，Dense沿用M05发布指针及完整Embedding签名。

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.mvp_cli init --auth .local/m08/demo-auth.json --budget-state .local/m08/demo-budget.json --max-calls 300 --max-tokens 600000 --max-cost 10
py -3.14 -m uv run --locked python -m deephelp_app.mvp_cli migrate
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.mvp_cli serve --auth .local/m08/demo-auth.json --budget-state .local/m08/demo-budget.json --port 8000
# 另一个终端调用，服务仍需运行。
py -3.14 -m uv run --locked python -m deephelp_app.mvp_cli ask --auth .local/m08/demo-auth.json --text "订单 000031 未享受优惠，请查一下"
```

浏览器打开`http://127.0.0.1:8000/`，密码框填auth文件中的令牌。页面显示回复/事实/证据/13段耗时与调用数，令牌不写浏览器存储。“发送新消息”生成新message ID，“重投同一消息”发送原payload。CLI重投必须同时保留message_id、occurred_at、session和正文，改变payload返回409。

主流程沿M00的13段，500明确单消息passthrough；600是唯一主意图服务，规则确定单类时选择，否则Dense召回加一次schema分类确认。余弦分数不当概率，不设置未经校准的接管阈值；无规则的长输入转人工，不截掉尾部后猜意图。原文/前导零/来源/更正沿用M04，缺乏明确字段语义的裸编号可以澄清。

缺槽位返回CLARIFY/WAITING_SLOT/run SUCCEEDED，要求用新消息重发完整问题；未知/多个诉求/否定查询不调用工具，无SOP/模型不可用形成正常契约。1100只输出核验事实和本地SOP结论。1200准备状态/版本，1300校验完整响应；MySQL终态事务提交后才返回，提交耗时另记ledger_committed trace。

迁移只创建`dh_m08_messages/runs/questions`。同(tenant,user,channel,message_id)唯一接收；相同hash回放终态或返回RUNNING引用，不新增问题/工具；hash不同返回IDEMPOTENCY_CONFLICT。终结事务更新run及Question版本，重复终结拒绝。应用资源重建可回放，取消保留已发送工具ID并落CANCELLED；失败ID不构成成功证据。进程硬中断的RUNNING不自动领取或重放。

每请求共享90秒总预算，模型子时限30秒，SOP/工具仍可收紧。存储收尾最多5秒，不延长业务调用。累计文件独占写、发送前持久预留，未知计费不退款；控制文件及其锁/临时文件要用不同路径。无--live的mvp_probe用36条M02标签/槽位回放验证串联，不证明模型质量；--live消费模型/MySQL/Milvus，--http使用真实HTTP，--samples选12条原始固定样本。验收入口见[LOCAL_SETUP](../../docs/LOCAL_SETUP.md#m08闭环启动与验收)。

| 尚未启用 | 用户边界 |
|---|---|
| 跨消息补槽、事件合并、记忆、Redis投影 | 每条消息独立完整问题；hint先核归属再拒绝恢复 |
| Hybrid/FastText/完整级联 | 在同一600服务中增量扩展 |
| 回复模型润色、LangGraph | 使用事实模板及现有SOPExecutorPort |
| 持久审批、业务写工具、崩溃领取 | 写工具禁用；RUNNING不盲目重放 |
| 企业接口、15+情境、500+未见评测 | 合成三类及固定样本不代表这些规模/质量 |

## M09中文BM25与融合对照

复用M05语料读取、版本签名、幂等导入与回读，新增服务端Jieba/BM25稀疏索引和WeightedRanker。输入是同一完整query，输出Dense、BM25、Hybrid三路命中、原始/融合分数、来源、rank及每意图最佳证据；不自动接管分类。每路ANN和最终返回K相同，融合联合候选最多2K；评测分别记录三路SDK耗时，生产retrieve为保留诊断也执行三路，不等于只发一次hybrid_search。

从根执行；除preview/init外必须显式--live，先按[LOCAL_SETUP](../../docs/LOCAL_SETUP.md#m09中文bm25与融合对照)检查真实依赖并初始化累计预算。以下使用本机默认.local/m09路径，输出须新文件：

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.hybrid_cli preview
py -3.14 -m uv run --locked python -m deephelp_app.hybrid_cli --help
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.hybrid_cli import --live --output .local/m09/import-report.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.hybrid_cli analyze --live --query "优惠券 不 未 not SKU-A7 订单00123456 免息 收银台" --output .local/m09/analyzer-report.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.hybrid_cli compare --live --query "手里的抵用凭证在付款页面一直灰着" --output .local/m09/compare-report.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.hybrid_cli tune --live --output .local/m09/dev-report.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.hybrid_cli evaluate --live --classify --output .local/m09/test-report.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.hybrid_cli activate --live --output .local/m09/publish-report.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.hybrid_cli verify --live --output .local/m09/verify-report.json
```

12条reference仅入库；18条dev选权重，24条test只评测。输入只经M04全半角ASCII/空白规范化，三路使用同一文本和一次Embedding，不改写问题；无关/多诉求不计单类Recall分母，计入后续分类。报告保留逐例、按意图/类别统计、失败ID与延迟；资源快照不冒充压测峰值。评分语义与边界见[CONTRACTS](../../docs/CONTRACTS.md)，实际指标见[交接](../../handoffs/M09.md)。

集合名绑定scope/signature/analyzer版本；迁移使用新dataset-version/manifest/selection。每次导入做现有数据占用与新版本峰值容量门禁，最多保留两个M09版本。`delete --allow-delete-synthetic --doc-id`只删当前scope既有合成记录，随后对照核对检索缺失；原记录/向量或同语料新manifest用于恢复，不能覆盖原验证证据。`rollback`恢复上一已验证语料和完整策略，不查询旧集合中的样本。

M08的serve/ask/probe追加`--pointer .local/m09/active.json`即可接入同一600服务，响应intent_decision.retrieval保留真实候选与版本；规则先行、缺槽位禁工具、MySQL账本、事实回复不变。更换指针后重建应用资源；默认Dense入口保留。M11可复用返回类型，但需自己的隔离scope/查询端口，不能把事件/用户历史装入本意图集合。

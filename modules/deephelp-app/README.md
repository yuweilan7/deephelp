# DeepHelp 业务运行、验收与学习

Python3.14.7，唯一业务契约`0.2.0-m02`。三类客诉使用下方M08入口；启动/凭据见[LOCAL_SETUP](../../docs/LOCAL_SETUP.md)，检查命令见[ROADMAP](../../docs/ROADMAP.md)，实际证据见对应handoff。

## 按改动选择验收

普通回归默认离线；真实模型验收按变更影响选择；完整真实评测按明确目标单独启动。范围判断见[ROADMAP决策表](../../docs/ROADMAP.md#按影响选择验收)，预算、失败复验和证据复用见[AGENTS](../../AGENTS.md#按改动范围验收)。以下示例展示选择语法，执行前仍需说明本次样本理由、预期调用/token范围、共享上限及不重验依据；不自动启动这些命令。

```powershell
# 离线查看可选范围，不读凭据或调用模型
py -3.14 -m uv run --locked python -m deephelp_app.probes.text_entity_probe --list-cases
py -3.14 -m uv run --locked python -m deephelp_app.evaluation.evaluation_cli audit --list-cases
# 单能力；$probe*是本次已初始化共享预算/新报告及运行上限
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.probes.live_probe --live --capability schema --stage feature --budget-state $probeBudgetFile --output $probeReportFile --max-calls $probeCalls --max-tokens $probeTokens --max-cost $probeCost
# 单字段内容；--case可重复，--strong-content才追加直接强端口验证
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.probes.text_entity_probe --live --case quoted-order --stage feature --budget-state $entityBudgetFile --output $entityReportFile --max-calls $entityCalls --max-tokens $entityTokens --max-cost $entityCost
# 同一主链的一组模式与完整会话，保留前序消息；--case/--mode可重复
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.evaluation.evaluation_cli run --live --mode memory_event --case m17-baseline-discount --case m17-baseline-missing --output $evaluationReportFile --budget-state $evaluationBudgetFile
```

M03不再依据stage自动跑四能力；`--all-capabilities`显式全能力，`--extended`需与它配合，或分别选extended_chat/thinking_schema。M04 live必须选`--case`或明确`--all-cases`；传强端口配置不再自动附加重复直连验证。M17 live必须选择`--mode`与`--case`，或明确`--full-evaluation`；完整四组使用后者，额外`--all-cases`才扩展到所有冻结会话，`--approvals`另显式选择审批矩阵。targeted报告仅证明所选内容/安全/持久回读/零调用重放，不能当四组消融或完整发布通过。离线默认范围保持完整，不影响pytest量。

仅选择实际装配了受影响能力的主链入口；M17默认关闭可选模型润色，不能用其报告替代M16润色Prompt的真实内容。回复或业务策略用一个相关主链入口取得必要真实内容，不再重复历史M08/M12/M14/M16/M18相同路径。能力未变可复用同会话内容证据；修复后只重跑失败和受影响相邻样本。M15固定动作可验证真实协议/数据库/下游，故障矩阵不逐例重新调用大模型；模型生成计划/事实若变化仍需本次真实内容。

M03/M04/M17报告新增按阶段、模型、样本的API观察与调用汇总。API提供的input/output/total/reasoning按原项记录，缺失/超时/诊断尾部缺失标null并保留已观察小计；reasoning可能包含在output中，不再相加。M17逐样本采集避免不同模式的100条诊断尾部代替全量usage；report中的累计预算包含其他尝试，不能冒充模型实际调用数。cost是配置估算，charged/token_upper_bound是预算占用，均非账单。原报告和失败证据留.local，不为多个入口重复生成模型响应。

下方完整探针/多组对照/发布命令均是明确启动相应目标的可选运行入口；普通小改动不串跑。完整发布仍要求精确版本/配置/资产/行为适用的成功证据和清单；单纯hash相同不足以证明行为相同，工具变化也不隐式启动新发布。历史main复验步骤已由当前AGENTS取代，main合并后不跑静态/pytest/构建/live。

## 业务启动入口

从仓库根使用下列入口。先核对既有隧道health、身份映射、累计预算和已验证资产；已有文件继续使用，不能用init重置预算。新机器初始化/增量迁移按下方M08/M15入口执行。

```powershell
py -3.14 -m uv run --locked python -m deephelp_app --help
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app serve --pointer .local/m09/active.json --auth .local/m08/auth.json --budget-state .local/m08/session-budget.json
py -3.14 -m uv run --locked python -m deephelp_app ask --session demo-session --text "订单 000031 未享受优惠，请查一下"
```

`python -m deephelp_app` 与保留的 `mvp_cli` 使用同一实现，提供init/migrate/serve/ask。serve装配现有三类业务和多轮主链，使用真实模型与中间件、合成下游事实；默认不启用写业务。适用审批需显式SOP/rights服务及原32-byte key，见M15；完整发布运行用下方release_cli serve，读取MySQL active并固定完整版本，不隐式切换业务channel。

直接 `uvicorn deephelp_app.app:create_app --factory` 是M01离线骨架，`/converse`保持501，用于学习生命周期。它不能作为三类业务启动命令。学习入口 `python -m deephelp_app.learning.experiments`；受控能力验收 `python -m deephelp_app.probes.live_probe --help`；冻结评测 `python -m deephelp_app.evaluation.evaluation_cli --help`。

运行资源和数据口径见[资源职责说明](src/deephelp_app/assets/README.md)。Hybrid启动核对已发布selection内容摘要、scope、语料、Analyzer、K/weight与Embedding签名，保留dev/test hash作为发布出处；开发选参/发布时仍核对冻结集的实际内容。完整发布继续验证全部运行工件、模型/词典、SOP/Prompt、配置及成功评测报告。包指纹采用runtime-v2，评测另记tooling_digest；运行代码/资源变化仍须新清单且旧清单不能装配新包；需新内容验收的行为按影响选择。只改工具或目录不默认启动发布，若明确发布则继续满足精确指纹/成功报告门禁。冻结集及学习资源无需参与业务启动。

## API

- M08 `GET /health`返回M08/READ_ONLY_SINGLE_MESSAGE，只证明应用存活；`POST /converse`接收channel/session_id/message_id/raw_text/带时区occurred_at，需要固定合成身份的Bearer令牌。业务结果包含run/question、事实/工具证据、13段耗时与调用数。
- 未传业务组装器的M01离线`create_app`保留501/NOT_IMPLEMENTED，run/question为null、调用0；不会因机器存在Key自动联网。

M01使用loopback/testclient本地测试身份，M08本机令牌映射为固定合成身份，不开发登录系统，服务仅绑定127.0.0.1。正文拒绝自报身份和诊断ID。X-Request-ID/X-Trace-ID仅供诊断，响应头/正文一致；回放沿用业务run/结果但使用本次传输ID。入口错误不回显输入或异常详情。

## 实现边界

| 文件 | 职责 |
|---|---|
| `app.py` / `settings.py` | ASGI 上下文、lifespan / 显式环境配置与 live 门禁 |
| `domain/models.py` / `domain/checks.py` / `errors.py` | 唯一契约、实体/意图/事实/证据、状态映射、归属/参数守卫和 HTTP 错误映射 |
| `domain/registry.py` / `demo/fixtures.py` / `evaluation/samples.py` / `assets/` | 三类意图目录、合成下游事实、固定样本及按职责隔离的资源 |
| `execution.py` | 共享 deadline/预算、Semaphore、只读 HTTP seam |
| `ports.py` / `learning/fakes.py` | ModelGateway/Repository Protocol / 学习替身 |
| `gateway.py` / `providers.py` / `learning/model_fakes.py` | M03 ChatPort/EmbeddingPort 实现、显式配置、合成 fixture |
| `probes/live_probe.py` / `local_paths.py` / `asset_integrity.py` | 显式验收 / 独立本机路径及运行指纹工具 |
| `trace.py` | 串行线程文件写、JSONL / 内存 trace |
| `text_entity.py` / `probes/text_entity_probe.py` | M04文本保真、原文实体证据、规则候选 / 受控内容验收 |
| `corpus.py` / `dense.py` / `milvus_dense.py` / `dense_cli.py` | M05语料校验、可续跑导入、Dense候选、Milvus及版本指针 / 内容验收 |
| `mcp_protocol.py` / `demo/mcp_server.py` / `tool_gateway.py` / `probes/mcp_smoke.py` | 只读注册表、合成业务stdio服务、工具网关 / 协议验收 |
| `learning/experiments.py` / `learning/mcp_faults.py` / `learning/rights_faults.py` | 异步实验与显式故障演练，说明见 [学习材料](../../docs/learning/M01_ASYNC_GUIDE.md) |

lifespan 通过 AsyncExitStack 管理共享 httpx 客户端，包括启动中途失败。每请求只创建一次 ExecutionBudget；排队、子调用与重试共用总 deadline，子 timeout 不延长它。只读且显式 retry_safe 的操作可有限重试；编程异常/取消继续传播，stream 在取消/错误时归还连接槽。默认连接 10、并发 2、deadline 5 秒、子 timeout 1 秒、调用 3 次、重试 1 次；均由设置校验。

trace 默认 `logs/m01-trace.jsonl`，仅记录诊断 ID、事件、长度和状态，不含正文、秘密或隐藏推理。FakeRepository 按 tenant/user/channel/message 隔离并深复制，同 payload 保留第一次关联结果，不同 payload 返回 IDEMPOTENCY_CONFLICT；不是持久账本。导入不建连接、读秘密或启动进程池。默认 fake 拒绝网络，不因 Key 存在切换 live。

## M02 离线演示

从仓库根执行：

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.evaluation.samples
```

入口校验 36 条 gold 样本和业务 fixture 的引用/归属、显式目录及 source/variant split 隔离，并运行纯状态映射：缺订单 → CLARIFY / WAITING_SLOT / run SUCCEEDED / 工具 0。不会预测意图、执行 SOP 或调用模型/云库。JSON 示例与准确率、macro-F1、覆盖/错误接管、实体、工具和场景指标口径见 [合成数据说明](src/deephelp_app/assets/README.md)。

下游从 `deephelp_app.domain.models` 导入类型，按 `domain.checks` 校验归属、实体、工具上下文/结果，不复制 DTO。金额只接受 Decimal 或十进制字符串，订单/券/SKU 保留字符串前导零；未知模型/embedding/费用版本保持 null。预算摘要只表达当前剩余量，不能替代原运行时预算。审批等待/写操作由M15显式持久审批装配启用；默认只读工具白名单保持。

## 验证范围

unit 验证类型、设置、预算和实验；integration 用真实本机 httpcore 池验证取消后连接槽复用；e2e 验证 ASGI 组装/Uvicorn 生命周期，均不代表云端业务效果。默认测试阻止外部 DNS/连接。实验可用 `py -3.14 -m uv run --locked python -m deephelp_app.learning.experiments`；CI 依据峰值、事件、清理及 heartbeat，不设毫秒门槛。

`tests/live`默认跳过，pytest的--live仍只测历史配置门禁。真实网关/业务闭环使用对应probe，M08入口见下方。GitHub Actions使用根锁执行静态检查和离线pytest。

## M03 模型网关

`providers.create_gateway(client, config, calls, env=...)` 复用调用者管理的 AsyncClient。transport retries 必须为 0；chat/embed 分别配置 endpoint/model/Key 字段，当前实现不自动切换模型。候选池及账号余额的使用遵循[AGENTS](../../AGENTS.md)，显式换配置后重新验收。调用前创建一次带 token_limit/cost_limit 的 ExecutionBudget 并在整个请求内复用。旧 complete(text, budget) 转为普通 ChatRequest；新调用者直接使用 ChatPort.chat / EmbeddingPort.embed。

ChatResult 包含 usage、finish_reason、provider_request_id、校验后的 schema 或 tool_calls。严格输出用 json_schema 并本地校验；显式 repair_once 最多一次修复，继续扣原预算。网关只解析工具协议，不执行 MCP 或业务写工具。Embedding 返回输入顺序的向量、provider/model/revision/dimension/normalization 签名、缓存计数与全零索引适配标记，规则和配置见 [模型矩阵](../../docs/MODEL_CAPABILITIES.md)。

请求先预留调用/token/费用上界，收到合法 usage 才结算预留。超时、取消或未知 usage 保留可能发生的计费占用，缓存命中不新增 API 费用。`gateway.record_diagnostics(path)` 写出最多 100 条结构 cassette，不含 prompt、正文、参数、凭据；`model_fakes.FakeGateway` 回放明确合成 fixture，费用为零。离线测试覆盖失败分类、重试上限、共享预算、一次修复、全零索引、向量校验和缓存隔离。

## M04文本与实体

从根运行不调用模型的演示：

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.probes.text_entity_probe
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

输入为UTF-8 JSONL/带表头CSV；无表头XLSX只接受一个工作表，必须用`--columns`指定字段→A/B等列字母映射，字段保存为文本，拒绝公式且不计算缓存值。每文件最多2MiB/1000行，content最多2000字符；缺字段、空行、未知/父节点标签、层级不符、重复ID/规范化正文、跨split来源/近义组均拒绝整批。数据字典见[合成数据说明](src/deephelp_app/assets/README.md)。

默认从M02的`cases.json`适配30条非空意图标签记录：6条reference入库、12条dev评测、12条regression仅保留manifest；另6条未知/否定不作为有标签参考语料。来源/近义组独立，这些公开样本不能当未见test。真实命令见[LOCAL_SETUP](../../docs/LOCAL_SETUP.md)。

`DenseImporter(embeddings, store, capacity).import_corpus(preview, scope, budget, manifest)`复用同一预算与EmbeddingPort，默认8条串行批次。先容量检查再建立/校验集合；稳定doc_id upsert，逐批回读正文、标签、版本、metadata和FP32向量。manifest原子保存pending/completed及每条完整FP32向量的SHA256；未知写入结果按相同主键续跑，已完成批次仍须回读并核对哈希。已验证且本次无写入的重导不重复flush，避免服务器0.1/s限流。manifest锁阻止同文件并发；异常/取消释放锁，进程崩溃遗留锁须核对后处理。

DenseScope绑定namespace/dataset_version/完整EmbeddingSignature/registry版本；集合名从整个scope派生，description绑定scope及索引digest。FloatVector FP32、FLAT/COSINE；同维度不同模型不共用集合。每批检查实际容量门禁，不以全项目固定集合数量阻止正常版本保留。容量不足停止导入，已有回退/引用资产不得为新版本盲目删除。

`DenseRetriever.retrieve(text, scope, budget, top_k=3)`实现DenseRetrieverPort，返回原始命中和`per_intent_max_v1`候选：每意图独立取最佳一条，再按cosine排序，同分按code/doc_id稳定排序；不求和样本分数。保留doc_id、code、raw_score、score_kind、metadata与版本；不产生最终IntentDecision或接管阈值。namespace是语料范围，不能替代用户鉴权。

`activate`只把回读完整且manifest VERIFIED的集合写入本机原子指针；`rollback`复验上一版本后切回，失败保留现指针。它不是业务账本或多主机发布系统。`verify/search/evaluate`不创建或修复集合，持久性复验不能用重新导入掩盖丢数据。维护删除只支持指定scope内已有synthetic记录及显式开关，恢复使用同语料的新manifest。BM25/Hybrid、最终意图识别与业务converse留后续模块。

## M06真实MCP只读工具

`get_order_benefits(order_id)`查询M02合成订单实付、优惠及活动；`check_coupon(order_id, coupon_id)`查询券状态、门槛和可用性。ID保持字符串和前导零。正常查询返回唯一ToolResult及实际call ID、事实、证据引用和内容hash；不存在、越权、坏参数或坏结果不发布成功事实。写工具不注册，refund_order等名称明确拒绝。

从仓库根运行，前两条只预览，不启动进程。`--live`启动正式SDK的本机stdio子进程，实际执行list_tools/call_tool；不调用模型/云库，也不运行P00。输出需使用新的.local文件名，重跑更换名字：

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.probes.mcp_smoke
py -3.14 -m uv run --locked python -m deephelp_app.probes.mcp_smoke --help
py -3.14 -m uv run --locked python -m deephelp_app.probes.mcp_smoke --live --stage feature --output .local/m06/feature.json
```

feature验正确金额/券状态/证据、双用户同进程并发、越权、不存在、模拟500/限流、缺字段/矛盾/体积、恶意文字、timeout和外部取消、取消后复用、ledger与退出，并核对服务退出。报告PASS还需退出码0。完整schema/身份篡改/签名重放/预算/启动失败/生命周期取消由`tests/integration/test_m06_stdio.py`的真实stdio回归验证，纯校验测试在`tests/unit/test_m06_contracts.py`。

下游通过`ToolPort.execute(request, question, budget, context=可信RequestEnvelope)`消费；`ToolGateway.open()`在服务生命周期中进入一次，多次调用复用一个Client/子进程。入口注入已验证context；网关核对Question的主体、session、槽位及状态，内部签名信封传给服务端再次检查订单/券归属。工具schema没有user_id、凭据、内部签名或故障开关。M06合成调用者不是登录认证；接入真实应用入口在M08实施。

默认启动限15秒、单工具5秒、并发4、返回体64KiB、ledger256次；每请求复用ExecutionBudget，排队占总deadline，调用前预留，重试0。smoke默认总120秒/48次，参数`--timeout`/`--max-calls`仅本轮上限。主动取消传播CancelledError；SDK发送取消并回收子进程，服务端相对deadline提供额外上限。

默认MockConfig无故障；feature加载[故障fixture](src/deephelp_app/assets/learning/mcp-faults.json)。支持延迟、下游500/限流、缺字段、矛盾、注入文字和超大结果，都是合成业务故障。`gateway.ledger()`读取签名保护的管理resource，不是模型工具；记录实际主体/参数、request/trace/operation/call及证据ID。可选ledger_path将有界合成快照写入新文件；密钥/签名不写盘。服务端查询完成而网关拒绝损坏输出时，ledger保留实际查询成功，diagnostics记录MODEL_OUTPUT_INVALID。ledger不提供业务副作用持久性或幂等；业务写工具仍需M15。

## M07最小SOP执行

三份 [SOP配置](src/deephelp_app/sop_data/) 对应优惠未享受、券不可用、订单活动查询；[schema](src/deephelp_app/sop_data/schema.json) 与 [Prompt](src/deephelp_app/sop_data/sop-react-v1.txt) 均版本化。配置限定 `lookup → evaluate`，分支只比较事实字段的 `eq/empty/nonempty`，不执行表达式。优惠未到账时已有查询不能解释原因，返回事实并转人工；券/活动按证据返回状态，查询可用不承诺结算成功。

根目录运行下面的演示：固定模型动作回放、本机真实SDK stdio和合成业务数据，外部模型/云库调用为0。输出三份完整SOPResult和真实ledger。

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.probes.sop_probe
py -3.14 -m uv run --locked python -m deephelp_app.probes.sop_probe --help
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
py -3.14 -m uv run --locked python -m deephelp_app.evaluation.hybrid_cli preview
py -3.14 -m uv run --locked python -m deephelp_app.evaluation.hybrid_cli --help
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.evaluation.hybrid_cli import --live --output .local/m09/import-report.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.evaluation.hybrid_cli analyze --live --query "优惠券 不 未 not SKU-A7 订单00123456 免息 收银台" --output .local/m09/analyzer-report.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.evaluation.hybrid_cli compare --live --query "手里的抵用凭证在付款页面一直灰着" --output .local/m09/compare-report.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.evaluation.hybrid_cli tune --live --output .local/m09/dev-report.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.evaluation.hybrid_cli evaluate --live --classify --output .local/m09/test-report.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.evaluation.hybrid_cli activate --live --output .local/m09/publish-report.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.evaluation.hybrid_cli verify --live --output .local/m09/verify-report.json
```

12条reference仅入库；18条dev选权重，24条test只评测。输入只经M04全半角ASCII/空白规范化，三路使用同一文本和一次Embedding，不改写问题；无关/多诉求不计单类Recall分母，计入后续分类。报告保留逐例、按意图/类别统计、失败ID与延迟；资源快照不冒充压测峰值。评分语义与边界见[CONTRACTS](../../docs/CONTRACTS.md)，实际指标见[交接](../../handoffs/M09.md)。

集合名绑定scope/signature/analyzer版本；迁移使用新dataset-version/manifest/selection。每次导入做现有数据占用与新版本峰值容量门禁，最多保留两个M09版本。`delete --allow-delete-synthetic --doc-id`只删当前scope既有合成记录，随后对照核对检索缺失；原记录/向量或同语料新manifest用于恢复，不能覆盖原验证证据。`rollback`恢复上一已验证语料和完整策略，不查询旧集合中的样本。

M08的serve/ask/probe追加`--pointer .local/m09/active.json`即可接入同一600服务，响应intent_decision.retrieval保留真实候选与版本；规则先行、缺槽位禁工具、MySQL账本、事实回复不变。更换指针后重建应用资源；默认Dense入口保留。M11可复用返回类型，但需自己的隔离scope/查询端口，不能把事件/用户历史装入本意图集合。

## M10多问题记忆

先保持隧道健康，使用已有`.local/m08/auth.json`与授权累计预算；首次升级执行增量迁移。新建M10专用auth/budget可用已有`mvp_cli init`，参数自行按验收范围设定。令牌只在本机配置中，不提交仓库。

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.mvp_cli migrate
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.mvp_cli serve --pointer .local/m09/active.json --auth .local/m10/auth.json --budget-state .local/m10/session-budget.json
```

调试页刷新当前会话的开放问题，选择后补充编号；选择“新问题”则独立新建。API仍为`POST /converse`，可带`question_hint`和`expected_question_version`；`GET /memory`提供窗口，状态操作见[CONTRACTS](../../docs/CONTRACTS.md#m10-多问题记忆契约)。CLI的`ask --question-hint <id> --question-version <version>`也支持续接。自动交错归属留M11，不会把无hint的补充偷偷并入旧问题。

投影单独运行，默认处理最多50个事件，累计预算沿用现有文件；遇到失败退出，按报告恢复依赖后再运行。每次远程投影有自己的子时限，MySQL事务已在调用前提交。事件集合与Embedding签名绑定。删除集合或更换签名后，用`rebuild --session`将该主体/session的最新问题从MySQL重新投影；超出limit会明确退出，需调整运行上限。

```powershell
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.memory_cli project --budget-state .local/m10/session-budget.json --limit 50
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.memory_cli rebuild --budget-state .local/m10/session-budget.json --session demo --limit 100
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.memory_cli show --budget-state .local/m10/session-budget.json --session demo --query "订单优惠问题"
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.probes.memory_probe --live --stage feature --auth .local/m10/auth.json --output .local/m10/feature.json
```

probe使用随机session、专用缓存前缀与独立事件集合，实际验HTTP补充/更正/事实、MySQL并发、Redis断连与缓存删除恢复、真实向量陈旧命中回查、lease/乱序/重试、新进程回读和长历史裁剪。合成事实/outbox留MySQL供回放，验证完成清理本次派生集合/键。恢复验收使用新的output，累计预算不清零；main不复验。默认pytest保持离线。
## M11 自动事件归属

独立入口只聚合和保存事件上下文，不调用主意图分类器或SOP。`converse`仍使用M10显式hint；M12再把聚合接入同一主流程。输入主体为本机CLI固定合成身份，未新增网络端点或企业登录。

归属裁决使用独立`event-judge.example.json`的qwen3.8-max；清洗/Embedding沿用原provider及1024维签名。`--judge-provider`可显式选择其他已验证端点，切换后重新验黄金序列；不自动降级。所有调用共用本次累计预算。

```powershell
# 无持久化、仅当前消息的离线预览
py -3.14 -m uv run --locked python -m deephelp_app.learning.event_cli preview --text "订单000007优惠没到账；订单000008参加的哪个活动"

# 按本次范围设置eventCalls/eventTokens/eventCost，再准备新的累计文件；已有文件不可归零
py -3.14 -m uv run --locked python -m deephelp_app.mvp_cli init --auth .local/m11/auth.json --budget-state .local/m11/session-budget.json --max-calls $eventCalls --max-tokens $eventTokens --max-cost $eventCost

# 真实归属/存储：依次发送后，第三条应回到券事件
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.learning.event_cli aggregate --live --budget-state .local/m11/session-budget.json --session m11-demo --text "我的优惠券不能用"
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.learning.event_cli aggregate --live --budget-state .local/m11/session-budget.json --session m11-demo --text "另外订单000008参加的哪个活动"
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.learning.event_cli aggregate --live --budget-state .local/m11/session-budget.json --session m11-demo --text "刚才那个券是C001，订单000007"

# 特性分支固定序列验收；恢复沿用同一预算和新报告路径
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.probes.event_probe --live --stage feature --budget-state .local/m11/session-budget.json --output .local/m11/feature.json
```

输出含`event_cluster`的当前事件、分组列表、原始消息引用、候选cosine/实体依据、排除原因、结构化裁决及图文本。自动判断不确定时，消息保存为独立待澄清问题且不作为后续自动候选；用其问题编号显式补充可解除待澄清。多个独立主诉先分成虚拟上下文并要求分条发送，不伪称一条消息已绑定多个持久问题。`--question-hint/--expected-version`可显式指定归属；重复消息须同时复用`--message-id`及`--occurred-at`，正文/session/hint变化仍拒绝。

MySQL成员、终态响应及outbox保留事实。派生事件摘要通过既有`memory_cli project`异步投影；读取不依赖Redis，索引失败不编造相似边。集合沿用M10签名隔离，M11 probe单独创建集合、只领取自己的终态outbox，删除/重建核验后清理该集合。普通有状态请求数据库失败即停止；只有显式preview可使用当前消息。黄金样本见`assets/learning/event_sequences.json`，其小合成效果不代表企业效果或完整自然语言覆盖。

## M12 完整聚合与意图级联

正式live的同一`/converse`现已自动归属，无需给每条补充手工附问题编号。只有600调用统一主意图服务，内部顺序为规则→当前Hybrid→必要的已确认事件增强Hybrid→有限schema兜底。`--pointer .local/m09/active.json`启用已校准Hybrid；旧默认M05指针仍兼容，但不直接按Dense分数接管。已绑定问题保持原意图，FastText默认disabled，可按M13入口显式启用。机制/短路表见[M12规格](../../docs/MODULES/M12_CASCADE_PIPELINE.md)。

归属判断和主意图兜底均使用`event-judge.example.json`配置的强模型（当前qwen3.8-max），按完整语义区分应减未减与一般金额询问/退款；提取、Embedding及SOP沿原provider配置。响应版本记录兜底模型及`m12-fallback-v2`，阶段计数仍分别核算实际调用，不额外重分类。

复用已有凭据/合成身份、M09指针和M10迁移。需要中间件时先health，所需能力证据缺失/失效时才补模型检查；新任务设置足够的调用/token/费用累计上限，init只生成新本机文件，恢复不得归零。已存在的`.local/m12/session-budget.json`可继续使用，init不能覆盖它。

```powershell
# 新累计文件可用hybrid_cli init创建；参数按当前任务设置，不固定长期额度
py -3.14 -m uv run --locked python -m deephelp_app.evaluation.hybrid_cli init --budget-state .local/m12/session-budget.json --max-calls $cascadeCalls --max-tokens $cascadeTokens --max-cost $cascadeCost
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.mvp_cli serve --pointer .local/m09/active.json --auth .local/m08/auth.json --budget-state .local/m12/session-budget.json

# 同session依次发送：两个开放问题、明确更正、券号补充、订单补充
py -3.14 -m uv run --locked python -m deephelp_app.mvp_cli ask --session m12-demo --text "我的订单未享受优惠"
py -3.14 -m uv run --locked python -m deephelp_app.mvp_cli ask --session m12-demo --text "另外订单000053的券不能用"
py -3.14 -m uv run --locked python -m deephelp_app.mvp_cli ask --session m12-demo --text "更正刚才的券问题：订单000042"
py -3.14 -m uv run --locked python -m deephelp_app.mvp_cli ask --session m12-demo --text "补充：券000009"
py -3.14 -m uv run --locked python -m deephelp_app.mvp_cli ask --session m12-demo --text "刚才优惠那单，订单000031"

# 特性分支真实HTTP内容验收；恢复使用新报告，沿用同一预算
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.probes.cascade_probe --live --stage feature --budget-state .local/m12/session-budget.json --output .local/m12/feature-new.json
# 旧单消息MVP独立session回归，避免把每条样本误作多轮续接
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.probes.mvp_probe --live --http --isolated-sessions --stage feature --pointer .local/m09/active.json --budget-state .local/m12/session-budget.json --output .local/m12/mvp-new.json
```

响应的`event_cluster`显示当前归属/原文引用/更正/排除原因，`intent_decision.cascade_steps`记录各层continue/接管/拒识和原始候选。`call_counts`为本请求主分类、模型、600检索及实际工具次数，重放为0；重放的stages保留原执行证据。`/health`显示M12/READ_ONLY_EVENT_CASCADE。缺业务槽位WAITING_SLOT、工具0；归属不确定单独保存待澄清消息，未确定的关系不会污染原问题。1200/1300及终态提交不能被普通短路绕过。

需要重新校准时只用dev，保留现有发布配置，先生成新的.local策略与网格报告；更换scope/语料/融合权重必须重验。该入口只做检索校准，不执行业务SOP，也不读取test标签选参；正式发布前人工审查报告并回归。

```powershell
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.evaluation.cascade_tune --live --budget-state .local/m12/session-budget.json --policy-output .local/m12/policy-new.json --output .local/m12/dev-new.json
py -3.14 -m uv run --locked pytest modules/deephelp-app/tests/unit/test_m12_cascade.py modules/deephelp-app/tests/integration/test_m12_pipeline.py
```

probe使用专用session/事件集合，只删除自己的派生集合；MySQL合成事实和outbox保留，Redis缓存按TTL失效。当前原意图集合与语料、凭据、来源原件不修改，不运行P00 full。真实MCP协议与真实云服务使用合成业务；小样本不代表企业效果，审批/写操作仍需M15。

## M13 FastText训练量化与可选兜底

默认关闭；显式`--fasttext-pointer`在原600兜底中加入本机CPU推理，安全top1接管，其余继续既有强模型。输入客诉原句，输出五类topK/原始概率、拒识理由和三个版本；缺订单/券号仍澄清、工具0。全半角、空白、Jieba/HMM与两份词典的签名必须与训练一致。真实小组对照及限制见[M13交接](../../handoffs/M13.md)，实现约束见[M13规格](../../docs/MODULES/M13_FASTTEXT.md)。

Windows沿根workspace/单锁及Python3.14.7，绑定固定fasttext-community0.11.8。无需GPU。训练仅两组有界配置，实际seed42/10线程；多线程不承诺逐字节复现。每次训练使用新目录，报告区分train/dev/test，test不选参数。Jieba依赖的旧正则SyntaxWarning不影响已验证结果。

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.evaluation.fasttext_cli audit
py -3.14 -m uv run --locked python -m deephelp_app.evaluation.fasttext_cli train --output .local/m13/experiment-new --timeout 180

# 按report.json的selected_raw选择真实候选；selected_quantized指向量化版本
$experimentReport = Get-Content .local/m13/experiment-new/report.json -Raw | ConvertFrom-Json
py -3.14 -m uv run --locked python -m deephelp_app.evaluation.fasttext_cli activate --manifest $experimentReport.selected_raw --pointer .local/m13/active.json
py -3.14 -m uv run --locked python -m deephelp_app.evaluation.fasttext_cli predict --pointer .local/m13/active.json --text "账单没扣掉承诺的优惠部分"
py -3.14 -m uv run --locked python -m deephelp_app.evaluation.fasttext_cli evaluate --pointer .local/m13/active.json --output .local/m13/evaluation-new.json

# 原子切换并验证上一版本回退；服务已加载的模型在重新装配时才变更
py -3.14 -m uv run --locked python -m deephelp_app.evaluation.fasttext_cli activate --manifest $experimentReport.selected_quantized --pointer .local/m13/active.json
py -3.14 -m uv run --locked python -m deephelp_app.evaluation.fasttext_cli rollback --pointer .local/m13/active.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.mvp_cli serve --pointer .local/m09/active.json --fasttext-pointer .local/m13/active.json --auth .local/m08/auth.json --budget-state .local/m13/session-budget.json
```

真实探针按影响选择，需中间件时先health/所需能力证据；沿现有凭据与专用业务表，新任务通过mvp_cli init设任务累计预算，已有预算继续使用、不得归零。init同时生成专用合成身份文件；预算参数按任务选定，不把历史数值作长期额度规则。

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.mvp_cli init --auth .local/m13/auth-new.json --budget-state .local/m13/session-budget-new.json --max-calls $m13Calls --max-tokens $m13Tokens --max-cost $m13Cost
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.probes.fasttext_probe --live --stage feature --experiment .local/m13/experiment-new --auth .local/m13/auth-new.json --budget-state .local/m13/session-budget-new.json --output .local/m13/feature-new.json
# 特性分支验收通过后按AGENTS合并/同步main，不复验。
py -3.14 -m uv run --locked pytest modules/deephelp-app/tests/unit/test_m13_fasttext.py
```

探针对同20条冻结test运行关闭/原模型/量化模型，分别报告最终意图与unknown/multiple原因、FastText覆盖/错误接管、真实API调用/token/成本及墙钟时间；仅测试FallbackPort，不冒充全业务时延。另在同一真实HTTP主链对比三模式的合成事实、缺槽位/未知、实际接管、MySQL终态/重放及MCP退出，清理各自事件集合。API调用由原累计预算计量；本地FastText毫秒不算模型API次数，原始概率保留softmax的1e-5偏移。模型/manifest及原始报告只留.local，无云端上传或新库迁移；更换数据/预处理必须新版本，任何失败保留旧指针。

## M14 SOP治理与场景验收

正式应用默认装配包内三份治理流程，仍在原13段主链的1000执行，不重新分类。输入如“订单DEMO-C01的券COUPON-C01不能用”；券标记未达门槛时先查券、再查订单，代码按合成口径比较金额；缺资料工具0、来源失败停止、矛盾转人工。`versions`显示注册表/SOP/快照/Prompt/工具签名，`sop_node_path`显示实际节点。原三份M07配置及旧问题版本继续可读。

配置只有lookup、branch、end和有限条件，没有任意表达式或代码；当前只接受有向无环图。校验工具/代码授权、槽位、事实类型/依赖顺序、重复ID、不可达节点、终点和步数/工具预算。`validate`、`publish`、`show`、`rollback`均离线；发布必须新注册表版本，改变的SOP也必须新版本，同一Prompt版本不能改内容。包内[注册表](src/deephelp_app/sop_data/registry-v1.json)可作为编辑模板，[schema](src/deephelp_app/sop_data/governance-schema.json)用于结构检查。

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.sop_cli validate
py -3.14 -m uv run --locked python -m deephelp_app.sop_cli publish --directory .local/m14/registry
py -3.14 -m uv run --locked python -m deephelp_app.sop_cli show --directory .local/m14/registry
# 编辑新注册表后先校验，再发布；下面的文件由使用者创建
py -3.14 -m uv run --locked python -m deephelp_app.sop_cli validate --source .local/m14/registry-new.json
py -3.14 -m uv run --locked python -m deephelp_app.sop_cli publish --source .local/m14/registry-new.json --directory .local/m14/registry
py -3.14 -m uv run --locked python -m deephelp_app.sop_cli rollback --directory .local/m14/registry
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.mvp_cli serve --pointer .local/m09/active.json --sop-directory .local/m14/registry --auth .local/m08/auth.json --budget-state .local/m14/session-budget.json
```

发布先回读完整内容再原子替换active/previous，重复发布保留previous。已有进程保留原快照，新装配才读取新active；历史快照保留供旧问题补槽，不应删除。显式目录不存在/损坏或所需旧快照缺失会阻断执行。发布/回退用同一写锁；崩溃残留锁须先核实原进程已退出再处理。这是本机发布机制。

19条固定合成情境覆盖8条正常业务分支、两种缺资料、查无记录/跨用户、两种注入、第一/第二来源失败、坏事实、两来源矛盾和审批前计划。它们不是19类主意图或企业场景。审批计划返回SOP `NEEDS_APPROVAL`，HTTP映射 `HANDOFF/HANDED_OFF`、`request_approval`及`sop_plan`；绑定主体/问题输入版本/参数hash/SOP快照/证据，不创建持久审批、申请或执行任何变更。M15须重新预检当前问题版本、绑定期限并实现批准/唯一领取，普通消息不能批准旧计划。默认未到账分支仍转人工；探针另发布只形成计划的版本验边界。

```powershell
# 无外部模型/云库，真实本机stdio+合成动作回放
py -3.14 -m uv run --locked python -m deephelp_app.probes.governed_sop_probe --output .local/m14/offline-new.json
py -3.14 -m uv run --locked pytest modules/deephelp-app/tests/integration/test_m14_sop_governance.py
# 已有预算沿用；新任务先按M13的mvp_cli init格式创建，参数按本次需要设置
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.probes.governed_sop_probe --live --stage feature --auth .local/m08/auth.json --budget-state .local/m14/session-budget.json --output .local/m14/feature-new.json
# 特性分支验收通过后直接合并/推送main，不再main复验
```

真实入口验19情境的模型动作/事实/ledger，再经实际HTTP验三流程、双来源、发布后旧问题补槽与新问题计划、MySQL新池回读、零调用重放及回退。整体timeout默认1200秒，各请求共享既有预算；transport重试0，固定故障情境关闭重试，执行器的有限重试另有回归。探针只清理自己创建的派生事件集合，保留MySQL合成事实/outbox、配置与恢复证据；不跑P00 full、云服务重启或企业写接口。

完整业务扩展使用独立v2[目录](src/deephelp_app/assets/evaluation/business-catalog-v2.json)、[合成事实](src/deephelp_app/assets/demo/business-fixtures-v2.json)及[注册表](src/deephelp_app/sop_data/business-registry-v2.json)。22个实际业务情境包含17个正常结果和5个记录/归属边界，三类意图复用原执行器；券增加已用/未开始/适用范围/冻结/撤销分支，双来源覆盖门槛低于/等于/高于/零门槛，活动覆盖单/多/无。探针显式加载v2，默认流程及旧19情境兼容：

```powershell
py -3.14 -m uv run --locked python -m deephelp_app.sop_cli validate --source modules/deephelp-app/src/deephelp_app/sop_data/business-registry-v2.json
py -3.14 -m uv run --locked python -m deephelp_app.probes.governed_sop_probe --business-catalog --output .local/m14/business-offline-new.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.probes.governed_sop_probe --business-catalog --live --budget-state .local/m17-scale/budget.json --output .local/m14/business-live-new.json
# 使用者显式发布新业务版本；保留原问题历史快照
py -3.14 -m uv run --locked python -m deephelp_app.sop_cli publish --source modules/deephelp-app/src/deephelp_app/sop_data/business-registry-v2.json --directory .local/m14/business-registry
```

## M16 事实回复与三类调试

运行同一只读主链，在页面查看意图、多轮和流程三个视图。金额、编号、业务结论由事实模板输出；`--reply-polish`可选，模型只选固定语气，新增事实/坏结构/失败直接回退。M14的计划回复明确尚未申请/批准/执行。机制见[M16规格](../../docs/MODULES/M16_OUTPUT_DEBUG.md)，接口见[契约](../../docs/CONTRACTS.md#m16-事实回复与调试契约)。

已有任务预算/身份可复用；新任务先按前文mvp_cli init创建并设本次足够运行上限。serve会调用真实模型/MySQL/Milvus及合成只读MCP；默认不开可选润色。浏览器地址为http://127.0.0.1:8000，令牌只从本机auth文件取出填入页面，不保存到浏览器存储。

```powershell
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.mvp_cli serve --pointer .local/m09/active.json --auth .local/m16/auth.json --budget-state .local/m16/session-budget.json --trace-path .local/m16/demo.trace.jsonl --reply-polish
# 返回run_id后，在另一个终端读取；$runId与$sessionId取本次响应和输入
py -3.14 -m uv run --locked python -m deephelp_app.debug_cli intent --run-id $runId --session $sessionId --auth .local/m16/auth.json
py -3.14 -m uv run --locked python -m deephelp_app.debug_cli turns --run-id $runId --session $sessionId --auth .local/m16/auth.json
py -3.14 -m uv run --locked python -m deephelp_app.debug_cli flow --run-id $runId --session $sessionId --auth .local/m16/auth.json --output .local/m16/flow-export-new.json
```

三个鉴权API为`GET /debug/runs/{run_id}/{intent|turns|flow}?session_id=...`，追加`export=true`下载脱敏JSON。同一run复用同一原始trace；重投不是新执行。编号是稳定替代ID，span偏移仍指向脱敏前原文。缺失段/丢弃数明确显示；404也可能表示非关键追踪已过期，业务结果仍从MySQL回放。trace及其`.jsonl.key`仅留本机，保留key才能在重新装配后识别原scope；不要手动删业务账本来清日志。

默认详细追踪最多4个2MiB文件、7天期限、128项非阻塞队列；单快照过大省略段并标注incomplete。磁盘失败不撤销已提交事实，关键业务记录不参与日志轮转。一个trace路径只供一个写进程；不同服务实例使用不同路径。三个视图记录显式决策/证据，不展示隐藏推理；审批/恢复仍留M15。

```powershell
# 离线：固定合成模型回放，正式SDK stdio及实际loopback HTTP；无云调用
py -3.14 -m uv run --locked python -m deephelp_app.probes.debug_probe --output .local/m16/offline-new.json
py -3.14 -m uv run --locked pytest modules/deephelp-app/tests/unit/test_m16_output_debug.py modules/deephelp-app/tests/integration/test_m16_debug_views.py
# 仅所需能力证据缺失/失效时补内容探针；需要中间件时先health；真实内容验收在特性分支完成
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.probes.debug_probe --live --stage feature --output .local/m16/feature-new.json
```

探针可显式指定--auth/--budget-state/--pointer/--providers，默认使用M16累计预算和M09检索指针；恢复继续同一预算，不重置。实际三类事实、自动补充、未知及正式SDK工具超时、四次真实受限润色、鉴权/脱敏、零调用重放、新追踪资源与MySQL新池回读逐项检查。工具超时通过专用合成对象延迟和真实child timeout触发，不冒充企业故障。清理自己创建的事件集合，保留MySQL合成事实/outbox；不跑P00 full或企业写接口。报告须新路径，原始内容/诊断与任务预算只留.local。

## M17 冻结评测与增量对照

输入公开合成单消息/多轮会话，输出同数据四组逐条结果、混淆矩阵、错例、空分母说明和发布判定。四组依次为Rule+Dense、Hybrid、Memory/EventCluster、再加FastText；检索使用同一M09集合及K，Dense不使用未经校准的分数接管，其他组沿用M12的dev冻结门限。模型、Prompt/SOP、调用上限保持一致；语义与业务gold由确定性规则/版本化business.json核验。规格见[M17](../../docs/MODULES/M17_EVALUATION.md)，数据边界见[sample_data](src/deephelp_app/assets/README.md#m17-冻结评测数据)。

```powershell
# 审计冻结hash、标签/对象依据、历史语料及split/模板组隔离
py -3.14 -m uv run --locked python -m deephelp_app.evaluation.evaluation_cli audit
# 默认离线全量：语义/SOP固定适配器及正式SDK本机stdio/loopback HTTP，非模型质量
py -3.14 -m uv run --locked python -m deephelp_app.evaluation.evaluation_cli run --output .local/m17/offline-new.json
py -3.14 -m uv run --locked pytest modules/deephelp-app/tests/unit/test_m17_evaluation.py modules/deephelp-app/tests/integration/test_m17_evaluation_flow.py
# 新机器/新任务初始化正数运行上限；已有任务继续原累计文件
py -3.14 -m uv run --locked python -m deephelp_app.mvp_cli init --auth .local/m17/auth-new.json --budget-state .local/m17/session-budget-new.json --max-calls $m17Calls --max-tokens $m17Tokens --max-cost $m17Cost
& infra/client/tunnel.ps1 -Action health
# 默认只跑数据中固定live_sample，显式--all-cases才跑真实全量
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.evaluation.evaluation_cli run --live --full-evaluation --stage feature --auth .local/m17/auth-new.json --budget-state .local/m17/session-budget-new.json --output .local/m17/live-feature-new.json
# 相同特性上的新实验可用--baseline对照；合并main后不再重复验收
```

默认真实入口复用.local/m09/active.json、.local/m13/active.json、.local/m17/auth.json及session-budget.json，可显式指定--pointer/--fasttext-pointer/--auth/--budget-state/--providers。必须先有已验证M09集合和M13模型；没有产物时真实对照停止。恢复不重置任务累计上限，报告用新路径。整轮默认1800秒，每请求90秒/40次/2重试；只读业务工具与模型共用原有预算。模型可调用性以内容探针与实际路径为准。

报告包含代码文件hash和Git状态、数据/模型/索引/Prompt/SOP/词典/策略签名、逐消息结果、实体来源和实际工具ledger；test/regression分别统计。Recall@K只统计实际发生的有标签检索，规则命中不虚构召回；额外memory query分别计入观测。费用是配置单价估算，非最终账单；离线真实调用/token/费用为null。会话完成要求每条业务检查与同/异事件关系都正确，单条正确不抵消错拆分。非关键日志不承担账本。

缺槽位工具调用、跨归属事实、无成功证据回答、错误工具对象、已确认regression退步或未完成抽样会拒绝交付。--baseline只接受相同代码/模型配置/数据选择/预算的成功报告，检查覆盖与完成率不退；真实模型不承诺逐字一致。每组保留MySQL合成事实/outbox，删除自己新建的事件集合并检查MCP退出；不修改原索引或训练模型。离线FastText列明确not_run_offline。扩量数据/运行入口如下，实际验证结果见PROJECT_STATE。

```powershell
$scaleData = 'modules/deephelp-app/src/deephelp_app/assets/evaluation/m17_scale_cases.json'
$scaleManifest = 'modules/deephelp-app/src/deephelp_app/assets/evaluation/m17_scale_manifest.json'
py -3.14 -m uv run --locked python -m deephelp_app.evaluation.evaluation_cli audit --data $scaleData --manifest $scaleManifest
# 先跑已知回归，再跑独立冻结test；完整离线范围为548会话1820消息/组
py -3.14 -m uv run --locked python -m deephelp_app.evaluation.evaluation_cli run --data $scaleData --manifest $scaleManifest --split regression --timeout 3600 --output .local/m17-scale/regression-new.json
py -3.14 -m uv run --locked python -m deephelp_app.evaluation.evaluation_cli run --data $scaleData --manifest $scaleManifest --split test --output .local/m17-scale/test-new.json
# 同一冻结29会话43消息/组的真实抽样，沿本任务已初始化的累计预算
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.evaluation.evaluation_cli run --data $scaleData --manifest $scaleManifest --live --auth .local/m17/auth.json --budget-state .local/m17-scale/budget.json --timeout 3600 --output .local/m17-scale/live-new.json
```

v2自动加载上述M14目录/注册表/合成工具数据，548个编号中立后不同会话序列覆盖22种实际业务情境；消息复用、模板组与合成比例分别报告。无事件组的自动续接/multi分段只作诊断，明确hint续接仍严格核验，原始错例与所有安全门禁保留。扩量报告逐行journal保存实际观察，长报告上限256MiB；test/regression分别统计。500+离线编排覆盖及固定真实抽样都不能解释成企业泛化或500+全量真实模型。

M17审批范围由独立冻结数据驱动，同一报告格式保存每个操作的真实效果/execute/query次数；普通pytest不连接云依赖。仅明确启动审批协议/完整发布评测时，下方入口新跑M15真实MySQL/HTTP/进程矩阵；普通改动按影响选择离线或固定动作，key保留，恢复沿用同一审批累计预算，新报告不能覆盖已有证据：

```powershell
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.evaluation.evaluation_cli approval --live --stage feature --output .local/m17-approval/feature-new.json
# 特性验收通过后按AGENTS合并/推送main，不再重复main验收
# 四组只读比较和审批检查合并为一次发布判定
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.evaluation.evaluation_cli run --live --full-evaluation --approvals --output .local/m17/combined-new.json
```

`--approval-budget-state`默认`.local/m17-approval/budget.json`，`--rights-key`默认原`.local/m15/rights.key`。审批范围不受四组能力差异影响，不重复四次副作用演练。越权发送/效果、重放增加、待审批副作用、UNKNOWN盲目重发和无事实成功任一发生都拒绝发布。17个操作含8个故障/7个关闭/并发/真实模型HTTP，6个越权attempt与9个恢复重放分开计量。

## M18 审核后的反馈闭环

输入专用合成客诉的真实MySQL run，输出脱敏候选、独立路线审核和可追踪的新语料/FastText/字面规则。重复采集同一run不倍增；高分、成功、澄清或人工转接都只是候选观察。审核标签独立于原系统判断；自动证据方式只接受包内已审查的三条合成来源，其他输入由操作者明确确认。这个入口演示人工触发机制，自动发布加固见[M18规格](../../docs/MODULES/M18_DATA_FLYWHEEL.md)。

```powershell
# 新任务先给足上限；已有累计预算和稳定脱敏key不能重置
py -3.14 -m uv run --locked python -m deephelp_app.flywheel_cli init --max-calls $m18Calls --max-tokens $m18Tokens --max-cost $m18Cost
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.flywheel_cli migrate --live
py -3.14 -m uv run --locked pytest modules/deephelp-app/tests/unit/test_m18_flywheel.py
# 真实服务/合成业务：采集、审核、两版构建、固定评测、启用/回退、更正/撤回
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.probes.flywheel_probe --live --stage feature --output .local/m18/demo-feature-new
# 特性验收通过后按AGENTS合并/推送main，不再重复main验收
# 自己操作：$runId须来自m18-collect channel和专用synthetic-user-a的已持久运行
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.flywheel_cli capture --live --run-id $runId --source-group $newSourceGroup --variant-group $newVariantGroup
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.flywheel_cli show --live --candidate-id $candidateId
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.flywheel_cli review --live --candidate-id $candidateId --revision $observedRevision --route corpus --decision approved --label DISCOUNT_MISSING --reviewer $reviewer --reason $reason --independent-from-heldout
# fasttext/rule分别审核；rule传--phrase，多个短语为全部命中，--exclude为字面排除
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.flywheel_cli build --live --build-candidate $candidateId --version $newVersion --output .local/m18/my-assets-new
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.flywheel_cli validate --live --assets .local/m18/my-assets-new/assets.json --baseline $previousValidation --output .local/m18/my-validation-new.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.flywheel_cli activate --live --assets .local/m18/my-assets-new/assets.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.flywheel_cli rollback --live
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.flywheel_cli withdraw --live --candidate-id $candidateId --revision $observedRevision --reviewer $reviewer --reason $reason
```

首次演示需要原M09真实集合、M13模型及M17本机鉴权；可覆盖--dense-pointer/--fasttext-pointer/--auth/--providers/--budget-state/--redaction-key。采集用原资产；构建用原1024维Embedding签名的新集合，并按实际双版本容量检查。FastText只扩展train，原dev/test不参与回流；数据按编号中立文本及来源/变体组隔离。规则仅2–80字字面短语，有限个数，不执行生成代码或正则。

validate对M17固定8案例11消息及另3条未见发布输入，检查事实、实体来源、工具成功证据、重放和新MySQL池。报告PASS与文件/模型/全文向量hash回读一致、审核快照仍有效才允许切换。显式演示指针`.local/m18/active.json`绑定三路文件，不替换默认M09指针；`flywheel_cli serve --live`加载它。演示完成回退到无新增反馈的完整版本，再更正/撤回一条来源以验证旧版不能重启用；旧工件仍在，可用show定位全部派生路径。未通过的报告不能替换已通过门禁。

演示指针是本机控制文件，MySQL保存候选/审核/任务/来源事实；完整发布使用下方独立入口，MySQL active是其唯一发布事实源。

双版本构建、全工程回归和真实验收在本机依次执行，给训练保留可用内存。同路径/签名的FastText分词词典在进程内复用只读词频；词典内容变化生成独立实例，既有实例和预处理签名保持原版本，预测结果不缓存。

## M18 完整版本发布

先用反馈入口构建并通过固定回归，再prepare完整清单；绑定三路工件、SOP/Prompt、两路模型配置和代码。register回读远端完整向量，activate/rollback只切换MySQL中的完整版本，必须传入刚观察到的revision。已有运行、补槽、审批/待对账保留原完整版本，新运行读取最新active。

```powershell
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.release_cli migrate --live
# $assets是已构建的assets.json；$sop是完整注册表JSON，先验证该SOP与当前代码/模型配置
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.release_cli validate --live --assets $assets --sop $sop --output .local/m18-release/my-validation-new.json
py -3.14 -m uv run --locked python -m deephelp_app.release_cli prepare --assets $assets --sop $sop --validation .local/m18-release/my-validation-new.json --version $newVersion --output .local/m18-release/my-release-new.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.release_cli register --live --manifest .local/m18-release/my-release-new.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.release_cli status --live --channel m18-demo
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.release_cli activate --live --channel m18-demo --release $releaseHash --expected-revision $observedRevision
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.release_cli rollback --live --channel m18-demo --expected-revision $observedRevision
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.release_cli references --live --release $releaseHash
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.release_cli serve --live --channel m18-demo --rights-port 8015 --rights-key .local/m15/rights.key
# 一次完整真实验收：两套向量资产、运行中切版、M15等待/对账、并发及实际进程中断
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.probes.release_probe --live --output .local/m18-release/feature-new
```

首次应用环境还需按M08/M15入口准备账本表、鉴权/累计预算和专用权益服务；release_probe自行准备专用运行数据，预算保留累计，不重置。普通pytest离线。所有验收只在特性分支运行，main合并后不复验。

清单不可覆盖，文件/模型/词典/Prompt/代码内容变化需新构建及新版本。运行引用在接收事务落账；retire仅标记无引用版本，active/previous及所有run引用均阻止退休，保守保留终态审计。它不删除文件、共享模型或来源；并发回退失败保留完整指针。完整发布没有每日自动训练或企业权益接口，真实验收使用专用合成业务。

## M15 持久审批与恢复

输入 M14 的绑定计划，输出 `PENDING_APPROVAL / WAITING_APPROVAL`。专用批准端点核验原 run、操作、主体、问题版本、参数和 SOP 快照；`resume` 才领取执行权。只对合成权益服务登记调整效果，默认只读装配仍保持原行为。普通“好的”或问题更正不会批准；待审批问题需先拒绝/撤销，再显式重开规划。流程/多轮调试视图附加当前 MySQL 操作、审批决定、领取/查询记录与最终事实。

```powershell
# 先按 LOCAL_SETUP 检查隧道；这一步只增加项目表，不重建环境
& infra/client/tunnel.ps1 -Action health
py -3.14 -m uv run --locked python -m deephelp_app.mvp_cli migrate
# 本机演示准备：保留既有32-byte服务key，发布独立审批版SOP
py -3.14 -m uv run --locked python -m deephelp_app.approval_cli init
# 在一个终端运行合成下游；它使用现有MySQL保存效果，重启不清零
py -3.14 -m uv run --locked python -m deephelp_app.synthetic_rights --port 8015 --key .local/m15/rights.key
# 另一个终端运行原应用；auth与累计预算先用M08 init准备，已有文件不重置
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.mvp_cli serve --pointer .local/m09/active.json --sop-directory .local/m15/demo-sop --rights-port 8015 --rights-key .local/m15/rights.key
py -3.14 -m uv run --locked python -m deephelp_app.mvp_cli ask --text '订单 DEMO-D01 优惠未到账，请查询'
# 以下值取自本次返回计划和status结果，不复用其他问题的值
py -3.14 -m uv run --locked python -m deephelp_app.approval_cli status --operation $operationId --run $runId --session $sessionId
py -3.14 -m uv run --locked python -m deephelp_app.approval_cli decide --decision approve --operation $operationId --run $runId --session $sessionId --expected-version $questionVersion --parameters-hash $parametersHash --sop-version $sopVersion --snapshot-hash $snapshotHash
py -3.14 -m uv run --locked python -m deephelp_app.approval_cli resume --operation $operationId --run $runId --session $sessionId
```

`decide --decision reject/revoke` 可结束未执行的计划。SOP切版后，原问题/版本/参数绑定未变时旧计划仍可拒绝、撤销或在过期后结束；新批准与执行必须匹配当前SOP。执行已开始时不允许谎称撤销效果；响应未知时保留 UNKNOWN，重新启动应用/下游后用同一 `resume` 查询对账。已提交效果不会再次执行；明确查无效果且当前授权/版本/期限仍有效时最多两次发送。运行 lease 默认120秒，硬杀进程后须等原 lease 到期，活跃执行期间重复 resume 返回409；每轮请求仍受子timeout和共享预算限制。

```powershell
# 最小saver门禁：两个命令必须在不同进程执行，使用新的thread和报告路径
py -3.14 -m uv run --locked python -m deephelp_app.probes.checkpoint_probe pause --thread $newThread --output .local/m15/gate-pause-new.json
py -3.14 -m uv run --locked python -m deephelp_app.probes.checkpoint_probe resume --thread $newThread --output .local/m15/gate-resume-new.json
# 全矩阵含真实模型HTTP、真实MySQL/Redis/Milvus与自建stdio、实际应用/下游进程退出和恢复
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_app.probes.approval_probe --live --stage feature --output .local/m15/feature-new.json
```

探针显式 `--live` 才运行真实依赖；默认 pytest 仍离线。`--budget-state/--key/--auth/--pointer/--providers` 可指定；新任务累计文件缺失时创建本次运行上限，恢复沿原预算、新报告，文件全部留.local。固定动作的多故障准备只验证恢复协议，真实模型HTTP单列；逐例报告网络execute/query次数、持久效果次数、审批/操作历史和最终状态。探针只删除自己的缓存键/事件集合，保留MySQL事实、key和恢复配置。MySQL saver是应用自建适配，锁定LangGraph版本的兼容试验不等于官方MySQL支持；未接企业支付、退款、补偿或通用13段RUNNING自动重领。

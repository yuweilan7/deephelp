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

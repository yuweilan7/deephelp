# DeepHelp M01

本包提供 Python 3.12 异步工程骨架。`/converse` 是明确的占位接口，不执行主意图分类、SOP、模型调用或业务写入。正式契约由 M02 原位扩展 `domain/models.py`；真实 ModelGateway 属于 M03。

## 从仓库根目录运行

Windows 已安装 uv 0.12.13，但不在 PATH：

```powershell
py -3.11 -m uv sync --locked --all-packages
py -3.11 -m uv run --locked python --version
py -3.11 -m uv run --locked uvicorn deephelp_app.app:create_app --factory --host 127.0.0.1 --port 8000
```

`py -3.11` 只是 uv 的启动器；成员应用使用根 Python 3.12.14。PATH 有 uv 时，可把 `py -3.11 -m uv` 换成 `uv`。新增依赖时由当前模块主写者从根执行 `uv lock`，不创建成员锁文件。

配置只从环境加载，不自动发现 dotenv。需要本机配置时显式运行：

```powershell
py -3.11 -m uv run --locked --env-file .env.local uvicorn deephelp_app.app:create_app --factory --host 127.0.0.1 --port 8000
```

`.env.example` 可复制为 `.env.local`；后者由 Git 忽略。设置中的密钥使用 `SecretStr`，密钥和上游地址均不参与设置序列化/repr。不要自行打印环境或完整设置对象。默认 `dev/test` 均使用 fake，客户端拒绝网络，不因存在密钥自动切换 live。

## API

`GET /health` 返回 HTTP 200：`{"status":"ok","module":"M01","capability":"NOT_IMPLEMENTED"}`。只证明应用骨架可用，不探测 MySQL/Redis/Milvus。

`POST /converse` 输入：

```json
{
  "channel": "local",
  "session_id": "session-1",
  "message_id": "message-1",
  "raw_text": "合成测试：查询订单",
  "occurred_at": "2026-09-13T01:00:00+08:00"
}
```

返回 HTTP 501、`outcome=ERROR`、`error.code=NOT_IMPLEMENTED`，`run_id/question_id=null`、事实/证据为空、调用数为 0。占位接口没有创建业务执行或幂等账本。`question_hint` 只是候选，不据此读取对象。未知版本与未计量 token/费用保持 null。

入口以独立本地测试身份提供器注入 tenant/user，仅允许 loopback/testclient；正文禁止自报身份、request/run/trace ID。该提供器不是生产鉴权，不应用于公网部署。有效 UUID 格式的 `X-Request-ID/X-Trace-ID` 可用于诊断关联；缺失或非法时重新生成，响应头与响应正文一致，不能用于授权或审批。

传输字段非法返回 422/`INVALID_ARGUMENT`；未获得本地身份返回 401/`UNAUTHENTICATED`；请求总 deadline 耗尽返回 504/`BUDGET_EXHAUSTED`；未预期异常返回 500/`INTERNAL_ERROR`。响应不回显验证错误中的原始输入或异常详情。M02 将发布完整错误映射。

## 目录和资源边界

```text
src/deephelp_app/
  app.py              API、纯 ASGI 请求上下文、启动/关闭资源
  settings.py         显式环境配置与 live 配置门禁
  domain/models.py    唯一最小 Request/Response DTO
  execution.py        deadline、共享预算、Semaphore、只读 HTTP seam
  ports.py            ModelGateway/Repository Protocol
  fakes.py            固定输出与内存合成存储
  trace.py            JSONL/内存 trace 接口
  experiments.py      三个离线可运行实验
tests/
  unit/ integration/ live/ e2e/
```

lifespan 创建一个共享 `httpx.AsyncClient`，退出与启动中途失败均由 `AsyncExitStack` 关闭资源。默认连接上限 10、LLM 并发 2、请求 deadline 5 秒、子调用 timeout 1 秒、最多 3 次子调用、全请求共 1 次重试。连接/并发/超时参数均有边界校验；M01 没有 LLM 网络调用。

每个 HTTP 请求只创建一次 `ExecutionBudget`，后续 Port 层传同一对象。Semaphore 排队也计入总 deadline；子调用 timeout 不延长总 deadline。重试仅对显式 `retry_safe=True` 的只读操作开放，超时和有限上游错误消耗共享预算；编程异常与 `CancelledError` 继续传播。HTTP stream context 在取消/错误时归还连接槽。这个传输 seam 不是业务写工具；M15 前禁止业务写工具。

JSONL 默认写 `logs/m01-trace.jsonl`（Git 忽略），只记录请求/trace ID、事件、输入长度与状态/错误代码，不记录正文、上游地址、密钥或隐藏推理。短文件写在线程里串行完成，取消时等待当前写完成再释放锁。将来可扩展结构化决策依据、候选、受控工具参数和结果摘要，不能要求模型披露思维链。

FakeRepository 只供合成测试；按 tenant/user/channel/message 隔离并深复制读写结果，不是 MySQL 事实源或 M08 幂等账本。导入模块不建连接、读密钥或启动进程池。

## 检查与实验

```powershell
py -3.11 -m uv run --locked ruff check conftest.py modules/deephelp-app
py -3.11 -m uv run --locked ruff format --check conftest.py modules/deephelp-app
py -3.11 -m uv run --locked mypy
py -3.11 -m uv run --locked pytest
py -3.11 -m uv run --locked pytest -m unit
py -3.11 -m uv run --locked pytest -m integration
py -3.11 -m uv run --locked pytest -m e2e
py -3.11 -m uv run --locked python -m deephelp_app.experiments
```

测试统一阻止外部 DNS/连接，只允许本机 loopback 合成 HTTP。integration 使用真实 httpcore 连接池验证取消归还连接槽；e2e 仅是进程内 ASGI 应用边界，不表示业务或云端 e2e。耗时实验输出对照值，CI 用并发峰值、事件顺序、清理状态和 heartbeat 判据，不用毫秒阈值。

`tests/live` 默认跳过；显式 `--live` 还要求 `DEEPHELP_ENABLE_LIVE=true`、HTTPS 目标、密钥、模型 ID 及环境调用/token/费用上限，并提供不超过这些上限的 `--live-max-calls/--live-max-tokens/--live-max-cost`。M01 的 live 测试只验证配置门禁，应用即使配置完整也拒绝启动并报告 `NOT_IMPLEMENTED`，不消耗真实额度。余额快照不是运行时计费器；M03 实现调用前必须验证模型、额度和预算。

最小 GitHub Actions 执行 locked sync、Ruff、mypy 和默认离线 pytest。本机结果及未执行项见根 `handoffs/M01.md`。

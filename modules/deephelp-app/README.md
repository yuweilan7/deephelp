# DeepHelp M01 应用

Python 3.14.7 异步骨架；启动/凭据见 [LOCAL_SETUP](../../docs/LOCAL_SETUP.md)，检查命令见 [ROADMAP](../../docs/ROADMAP.md)，验证证据见 [M01 handoff](../../handoffs/M01.md)。M02 原位扩展唯一 DTO，真实 ModelGateway 属于 M03。

## API

- `GET /health`：200，`{"status":"ok","module":"M01","capability":"NOT_IMPLEMENTED"}`；只证明应用存活。
- `POST /converse`：接收 channel、session_id、message_id、raw_text、带时区 occurred_at；返回 501 / NOT_IMPLEMENTED，run/question 为 null，事实/证据为空、调用数 0。完整传输字段见 `domain/models.py`。不执行主分类、SOP、模型调用或业务记账。

入口只允许 loopback/testclient 的独立本地测试身份，不用于公网鉴权；正文拒绝自报身份和诊断 ID。UUID 格式的 X-Request-ID / X-Trace-ID 仅供关联，缺失/非法则生成新值，响应头和正文一致。非法传输、缺身份、总 deadline、意外异常分别映射 422/401/504/500，错误消息不回显输入或异常详情。

## 实现边界

| 文件 | 职责 |
|---|---|
| `app.py` / `settings.py` | ASGI 上下文、lifespan / 显式环境配置与 live 门禁 |
| `domain/models.py` / `errors.py` | 唯一最小 DTO / 安全错误封装 |
| `execution.py` | 共享 deadline/预算、Semaphore、只读 HTTP seam |
| `ports.py` / `fakes.py` | ModelGateway/Repository Protocol / 合成替身 |
| `trace.py` | 串行线程文件写、JSONL / 内存 trace |
| `experiments.py` | 三个离线异步实验，说明见 [学习材料](../../docs/learning/M01_ASYNC_GUIDE.md) |

lifespan 通过 AsyncExitStack 管理共享 httpx 客户端，包括启动中途失败。每请求只创建一次 ExecutionBudget；排队、子调用与重试共用总 deadline，子 timeout 不延长它。只读且显式 retry_safe 的操作可有限重试；编程异常/取消继续传播，stream 在取消/错误时归还连接槽。默认连接 10、并发 2、deadline 5 秒、子 timeout 1 秒、调用 3 次、重试 1 次；均由设置校验。

trace 默认 `logs/m01-trace.jsonl`，仅记录诊断 ID、事件、长度和状态，不含正文、秘密或隐藏推理。FakeRepository 按 tenant/user/channel/message 隔离并深复制，不是持久账本。导入不建连接、读秘密或启动进程池。默认 fake 拒绝网络，不因 Key 存在切换 live。

## 验证范围

unit 验证类型、设置、预算和实验；integration 用真实本机 httpcore 池验证取消后连接槽复用；e2e 验证 ASGI 组装/Uvicorn 生命周期，均不代表云端业务效果。默认测试阻止外部 DNS/连接。实验可用 `py -3.11 -m uv run --locked python -m deephelp_app.experiments`；CI 依据峰值、事件、清理及 heartbeat，不设毫秒门槛。

`tests/live` 默认跳过。显式 --live 要求开关、HTTPS 目标、Key、模型及环境调用/token/费用上限，CLI 预算不得超环境值。M01 只测配置门禁；完整 live 配置仍报告 NOT_IMPLEMENTED，不调用模型。运行时能力/计费由 M03 补齐。GitHub Actions 使用根锁执行静态检查和离线 pytest。

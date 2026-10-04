# DeepHelp标准观测

M21第1特性使用OTel Python SDK/HTTP OTLP exporter **1.45.0**与Collector **0.128.0**。当前只有Collector，file exporter用于管道验收；Tempo/Prometheus/Grafana为下一顺序特性，配置不是集成通过证据。隔离部署目录`/opt/deephelp-observability`，compose project为`deephelp-observability`，不接现有中间件网络、不迁移应用。

应用及两个MCP按原README启动，显式设置：

```powershell
$env:DEEPHELP_OTLP_ENDPOINT='http://127.0.0.1:14318'
```

该地址通过私有SSH隧道连接远端loopback的14318端口。无此变量时不启动导出线程，原业务/JSONL照常工作。URL不携带凭据。service.name分别为deephelp-app、deephelp-business-mcp、deephelp-retrieval-mcp；demo stdio为deephelp-demo-mcp。service.instance.id区分进程累计计数重置，不含用户/订单。完整采集实际执行，SDK provider私有，不重复安装自动instrumentation。

原trace_id保留；TraceEvent/DebugSnapshot及三视图增加可空otel_trace_id，读取仍受原身份/session守卫。MCP的独立deephelp/observation元数据只传traceparent，不改变工具参数/签名身份。无效上下文建立新trace，不能授权。标准阶段span包含实体/级联/记忆、SOP、实际模型尝试、检索和MCP校验、数据库及图/Saver动作；跳过/停用是stage.state事件，不伪造执行span。结构化规则/版本/事实继续从原debug投影解释，不导出正文、隐藏推理、密码、SQL参数或异常消息。

每进程耗时使用单调时钟，墙钟用于排序；并发子步骤不相加当总耗时，客户端往返减服务处理不称精确网络耗时。审批interrupt时结束span，恢复用新trace和Span Link关联原实际pause；独立dh_m21_observation_links是可丢失诊断，短超时读取，不改变checkpoint/账本。SDK有限队列/批量/导出超时与退出等待；满队列和失败分别计数，不阻塞或重放业务。

| 指标 | 分母与边界 |
|---|---|
| deephelp.operations、duration秒直方图、inflight | 每固定动作一次；阶段汇总不是供应商调用，缓存无新model.attempt |
| deephelp.request.outcomes | 业务结果与HTTP/技术状态分开；澄清/审批等待不当故障 |
| deephelp.model.tokens、usage_unknown | 实际input/output及未知尝试；不加预算上界，不重复加reasoning |
| deephelp.export.failed_spans/dropped_spans | 导出错误与队列实际丢失，允许诊断缺失 |

状态completed/rejected/failed/cancelled/interrupted分开。标签只有固定动作、状态、业务结果、配置模型和服务实例，禁止用户/message/run/order。费用估算与预留仍在既有预算/受保护debug，不冒称账单。低样本分位数/长期可用率尚未提供。

## 固定真实验收

先health；按本轮上限用应用init创建专用累计文件，不重置旧预算。默认复用M20服务凭据/允许主体、M09指针及语料。仅显式live才创建合成订单和专用审批记录：

```powershell
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_tools.probes.observability_probe --live --mode protocol --output .local/m21/protocol-new.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_tools.probes.observability_probe --live --mode approval --output .local/m21/approval-new.json
```

protocol核对实际173.45数据库事实、两个MCP、三路候选/排序/分数/签名、缓存零新增Embedding；elapsed_ms单独观测。approval用固定计划/真实stdio、MySQL、合成权益HTTP及独立进程，验证interrupt、明确决定、resume、重放、UNKNOWN查询对账，模型0次。它们不证明新增模型质量，仍适用的内容证据见M20。失败保留必要日志，成功清理本次精确创建的日志；控制文件/持久事实保留。

框架依据：[OTel Python](https://opentelemetry.io/docs/languages/python/instrumentation/)、[Collector配置](https://opentelemetry.io/docs/collector/configuration/)、[W3C Trace Context](https://www.w3.org/TR/trace-context/)。完整机制和平台退出条件见[M21规格](../../docs/MODULES/M21_OBSERVABILITY.md)。

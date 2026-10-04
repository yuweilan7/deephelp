# DeepHelp标准观测

M21两项已接通并实测：OTel Python SDK/HTTP OTLP exporter **1.45.0**→单一Collector **0.128.0**→Tempo **2.8.2**与Prometheus **3.4.2**→私有Grafana **12.0.2**。具体证据见[M21交接](../../handoffs/M21.md)。隔离部署目录`/opt/deephelp-observability`，compose project为`deephelp-observability`，不接现有中间件网络、不迁移应用。

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

状态completed/rejected/failed/cancelled/interrupted分开。标签只有固定动作、状态、业务结果、配置模型和服务实例，禁止用户/message/run/order。费用估算与预留仍在既有预算/受保护debug，不冒称账单。低样本分位数显示无数据，不报告长期可用率。

## 固定真实验收

先health；按本轮上限用应用init创建专用累计文件，不重置旧预算。默认复用M20服务凭据/允许主体、M09指针及语料。仅显式live才创建合成订单和专用审批记录：

```powershell
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_tools.probes.observability_probe --live --mode protocol --output .local/m21/protocol-new.json
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_tools.probes.observability_probe --live --mode approval --output .local/m21/approval-new.json
```

protocol核对实际173.45数据库事实、两个MCP、三路候选/排序/分数/签名、缓存零新增Embedding；elapsed_ms单独观测。approval用固定计划/真实stdio、MySQL、合成权益HTTP及独立进程，验证interrupt、明确决定、resume、重放、UNKNOWN查询对账，模型0次。它们不证明新增模型质量，仍适用的内容证据见M20。失败保留必要日志，成功清理本次精确创建的日志；控制文件/持久事实保留。

框架依据：[OTel Python](https://opentelemetry.io/docs/languages/python/instrumentation/)、[Collector配置](https://opentelemetry.io/docs/collector/configuration/)、[W3C Trace Context](https://www.w3.org/TR/trace-context/)。完整机制和平台退出条件见[M21规格](../../docs/MODULES/M21_OBSERVABILITY.md)。

## 平台与访问

固定配置位于本目录，数据只在隔离目录data下；Tempo WAL/blocks保留24小时，Prometheus历史保留3天且TSDB目标256MB（WAL/head另占空间），Grafana数据库持久保存。Collector内存队列256批次、有限30秒重试，重启可丢未发诊断。原JSONL继续用于有界事实解释，不再将完整span另存文件。compose内存上限是保护参数，实测RSS/cgroup见handoff，不当需求或企业账号额度。

| 私有loopback端口 | 用途 |
|---|---|
| 14318 | SDK OTLP HTTP |
| 18888 | Collector原生指标，仅管理 |
| 13200 | Tempo结构查询 |
| 19090 | Prometheus历史/当前查询 |
| 13000 | Grafana管理员入口 |

按现有SSH连接配置转发相同端口，不在公网放行；本机已有本轮独立隧道。Grafana关闭匿名/注册，管理员凭据单独保存在隔离目录grafana.env（0600），不提交Git。个人管理员可通过现有SSH/sudo读取该文件；普通应用身份没有Grafana账号。目录首次准备时先核对绝对路径及软链接，创建data/tempo、data/prometheus、data/grafana并分别交给镜像用户10001、65534、472；权限750，不操作既有中间件目录。部署前检查云容量，复制本目录配置并准备私有grafana.env，再在隔离目录执行：

```bash
sudo docker compose config --quiet
sudo docker compose up -d
sudo docker compose ps
```

维护只用指定compose目录和服务名restart/stop/start；不删除数据卷。Prometheus抓取SDK、Collector及Tempo各一个来源，不启Tempo span metrics重复计数。Grafana自动加载两个数据源及DeepHelp看板，12面板覆盖实际动作、状态、业务outcome、inflight、usage、导出损失、样本/分位数和队列/组件采集。P50/P95/P99与样本数使用相同窗口的increase、Prometheus桶插值，样本少于100显示无数据；P99稀疏结果仍无稳定性/SLA承诺。组件up是抓取状态，不代表模型可用。

应用设置DEEPHELP_TEMPO_ENDPOINT=http://127.0.0.1:13200、DEEPHELP_GRAFANA_URL=http://127.0.0.1:13000。GET /observability/runs/{run_id}?session_id=...&cursor=0&page_size=100沿原身份源鉴权并核对scope/审批归属，再从已保存otel_trace_id查询Tempo；禁止任意trace/search参数。cursor上限20000，page_size上限200，响应最多2MiB、单查询3秒，不跟随重定向。返回结构化span/parent/link、总数/next_cursor与私有Grafana Explore深链接；凭据/正文不返回，Cache-Control=no-store。平台缺失/故障/过大显示unavailable/truncated，不改变receipt或重新调用模型。SDK/JSONL/Tempo任一丢诊断不等同账本丢失。

```powershell
py -3.14 -m uv run --locked --env-file .env.local python -m deephelp_tools.probes.observability_probe --live --mode platform --output .local/m21/platform-new.json
```

platform复用--reuse-report的实际MCP trace，并用M02-005固定ReplayAssembly/平台专用auth验证主链→受保护Tempo分页→重放，模型API为0；平台专用auth默认.local/m21/auth.json，按应用init创建并核对固定样本身份。普通protocol/approval仍复用M20 auth。两个故障模式需先明确停止本隔离栈对应Collector或Tempo，分别运行--mode failure/backend-failure，再恢复并核对队列/原trace；不会自行停止云组件或批量删除业务数据。故障模式只读本任务M21合成订单，验证金额173.45和唯一工具派发，不消耗模型。

实际Grafana Tempo插件的health方法未实现，用数据源proxy及traceId查询返回实际帧验证；不能把该404当连通失败。Collector重启后的当前暴露缓存可清空，历史持久核验应查询Prometheus保存的窗口，例如sum(max_over_time(deephelp_operations_total[1h]))，而非要求旧进程瞬时样本永远存在。

平台依据：[Tempo API](https://grafana.com/docs/tempo/latest/api_docs/)、[Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)、[Collector内部指标](https://opentelemetry.io/docs/collector/internal-telemetry/)。

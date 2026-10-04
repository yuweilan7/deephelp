# M30 指标与原生观察

正式 trace 从应用/两 MCP 的 Langfuse SDK 进入原平台；W3C 传播与内部 OTel 保持。三个进程分别以 loopback 9464/9465/9466 暴露 PrometheusMetricReader，Prometheus 19092/Grafana 13002 读取本目录 metrics 模板与私有部署根 `/srv/deephelp-metrics`。指标含原动作/状态/用量/未知 usage/重试/直方图，另有进程资源、抓取 up 与 Milvus 原生资源；不叠加 Collector 数值。

零成功比例分母无值；取消/拒绝独立计数。分位数与样本数使用相同窗口，低于100个动作无值。Langfuse内部队列与丢 span 未可精确计数，看板不作推断。进程资源/主机 OOM/容量需与平台内容一起读，up 不证明业务或模型质量。

`/observability/runs/{run_id}`仍先核对身份、session和审批归属，再查询PG登记的原生trace；有界分页/no-store/脱敏保持。未登记旧run显示 `source=legacy-tempo,status=retired`，不会查询旧端点或伪造原生关联。旧Tempo数据与恢复代码保存于受保护旧根和 `bak/mysql-otel`，历史 M21 实测见[交接](../../handoffs/M21.md)。

安装/控制/一致备份/隔离恢复、访问和安装回退见[部署入口](../deployment/README.md)，当前切换事实见[M30交接](../../handoffs/M30.md)。新指标栈不要与旧Grafana/Prometheus挂同一目录。只绑定私有loopback并使用原SSH入口，不开公网。

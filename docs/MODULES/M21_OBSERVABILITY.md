# M21｜OpenTelemetry链路与Grafana观测栈

**阶段：** 先接标准观测，再集成真实观测平台；两个顺序特性，不增加新业务决策。

**前置：** M20业务/检索MCP均已交付；复用M16三视图、M08/M12的13段trace、M15审批图/MySQLSaver、M18版本/审核引用。实施读取对应直接handoff和实际接口。

**来源：** 用户2026-10-04要求，工程扩展。替代旧M21框架对照。

## 观察一次真实执行

复用已有TraceSink/TraceEvent/DebugSnapshot，接入OpenTelemetry Python SDK与标准trace/span父子关系，不能只换字段名就称框架接入。保留现有业务run/question/message/operation与trace的关联；若现有trace_id不是标准OTel格式，增量保存映射/otel_trace_id，不破坏既有DTO或将trace当账本/授权依据。新增依赖先核对Python3.14.7、Linux和异步/取消兼容性，不未经验证更换引擎。

覆盖请求→13段→模型/实体/级联→Embedding、Dense、BM25、Hybrid融合、Memory→SOP节点→MCP→业务MySQL→回复与账本提交。只有实际发生的动作生成执行记录，跳过/缓存/停用/失败/取消有明确状态；未实现的rerank不画成已执行。记录脱敏输入输出摘要、耗时、尝试、错误/上游request_id、模型/策略/索引/SOP/发布版本、事实出处与可定位代码入口。结构化决策及规则证据可解释，隐藏推理与凭据不进入页面或采集器。

跨async Task、MCP Streamable HTTP及显式stdio传递观测上下文；使用经校验的独立元数据/HTTP头，不能把学习字段混入模型业务参数。分别标记service.name=应用/业务MCP/检索MCP，记录caller span→server span→schema校验/认证归属→实际数据库/检索→结果校验。工具名、schema/version/hash、调用ID与safe payload帮助学习；原始token/密码、全量SQL参数和任意敏感文本不导出。

单调时钟计本进程耗时，墙钟用于跨进程排序；显示客户端往返时间、服务端处理和数据库/模型子步骤，不将二者差值直接断言为精确网络耗时。父子包含与并发有明确语义，子步骤耗时不能直接相加当总耗时；取消/重试都有实际记录，重复receipt不得伪造新模型span。手工与自动instrumentation覆盖同一动作时避免重复span/调用计数。

## LangGraph与checkpoint的实际范围

现有approval.py已用LangGraph StateGraph的approval_wait→execute_operation→reconcile_effect，compile(checkpointer=MySQLSaver)，并有interrupt/Command(resume)；checkpoints.py的自建MySQLSaver继承正式BaseCheckpointSaver，保存typed checkpoint/parent/pending writes。这是已存在实现，不能宣称整个13段主链都是LangGraph，也不能把它叫官方MySQL插件。

观测增加图节点进入/退出、interrupt、显式审批决定、resume、checkpoint读/写与耗时、parent/pending writes摘要、UNKNOWN查询/对账、重复resume无新效果。优先在既有graph节点与Saver接缝显式span，不假设安装自动instrumentation后业务节点和持久协议便全部可见；框架callback若采用也须核对锁定版本语义。

等待用户审批不保持一个跨数小时/进程的未结束span。首次执行与恢复各自新trace，通过原run/operation、checkpoint关联和Span Link串联；运行耗时、人工等待和恢复时间分别计量。观测不得触发resume/批准或改变checkpoint内容与业务账本；合成权益演示显式标记，图可见不代表真实订单已变更。

## 选定框架与责任

首轮选用一套自托管的标准组合，不在学习前端重新实现完整指标存储或trace搜索：

| 组件 | 责任与模式 |
|---|---|
| OpenTelemetry Python SDK | 应用/两MCP服务的显式业务span、必要HTTP/DB调用观测及指标；具体自动适配包先验证，不能声称所有异步SDK自动覆盖 |
| OpenTelemetry Collector | OTLP接收、批量/缓冲、脱敏及有限队列/导出；不同时安装另一套重复Collector |
| Tempo | 单体trace存储与查询，个人实验先用本地持久目录，不引入Kafka/对象存储集群 |
| Prometheus | 请求/调用/错误/usage/耗时直方图等指标；OTel指标经Collector暴露采集，既有中间件原生指标可单独直接采集，每类一个权威来源 |
| Grafana | 已配置Tempo/Prometheus数据源、请求/服务/依赖仪表盘、trace深链接；私有访问，不公开绕过业务归属守卫 |

DeepHelp自己的页面负责教程、源码入口、事实/状态解释与受身份保护的本次trace投影；Grafana负责专业指标/trace探索，两者用同一观测关联。保留现有有界JSONL/debug snapshot用于必要事实解释与失败诊断，不再平行自建另一套span仓库。应用先授权当前身份的run，再查询Tempo；trace ID本身不是读权限。Grafana为个人管理入口，不开放给普通测试身份越权查看。

Langfuse保留为将来明确启动的LLM专项对照，首轮不部署。选择依据是当前重点包含MCP/schema/数据库/异步图/checkpoint与资源指标，统一观测栈覆盖面更贴合；Langfuse当前完整自托管额外包含web/worker、PostgreSQL、ClickHouse、Redis与S3兼容存储，不能仅根据业务CPU低就断言其空闲底座也适合共置。首轮不并行安装Jaeger、Loki或LangSmith；已有结构化日志与trace关联够用，确有日志检索需求时再选独立特性。不是降低模型能力或截断有效业务输出。

官方依据：[OTel Collector](https://opentelemetry.io/docs/collector/)、[Tempo部署模式](https://grafana.com/docs/tempo/latest/set-up-for-tracing/setup-tempo/plan/)、[Prometheus](https://prometheus.io/docs/introduction/overview/)、[Grafana traces](https://grafana.com/docs/grafana/latest/visualizations/simplified-exploration/traces/)、[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Langfuse部署](https://langfuse.com/self-hosting/deployment/docker-compose)。实施时固定实际验证版本/镜像，不无条件追latest。

## 指标与存储

定义接口/模型/检索/MCP各自请求数、调用数、完成率/技术成功率、错误/重试、in-flight、总耗时及阶段耗时分布、实际token usage与未知项；费用估算和预算预留单列。业务澄清、人工交接、权限拒绝不能统一算故障；HTTP200不能统一算成功。

展示窗口、样本数、P50/P95/P99与算法；低样本时明确不足，不给个人偶尔查询伪造稳定TP99或SLA。请求成功率与基于探测/时间窗口的可用率区分，未采集连续探测就不报告长期可用率。指标标签不使用user/message/run/order等高基数字段。

个人低并发学习执行先完整采集实际trace，TTL/容量/队列/保留窗口按实测设置；不通过默认抽样省掉需要解释的步骤。平台不可达、队列满或导出失败可丢诊断并明确标记，不能阻塞业务/重复调用模型或破坏事务。指标/trace重启读取与查询分页实际验证；长期审批/UNKNOWN唯一恢复信息继续在业务账本，不依赖Tempo保留期限。

用户截图显示4核/8GB、CPU约3.449%、内存约1019.166MB、系统盘9.6/60GB，仅为截图时点；支持积极启动集成，不证明索引/训练/新服务同时运行的峰值。未来真实集成先只读核对host与cgroup当前/峰值、盘/WAL/索引、系统余量，再测完整组合。不得将旧compose上限当当前RSS，也不得预写未经实测的框架内存数值。性能/容量验收不需要调用模型；原真实主链内容路径另按影响选择。

健康探测不调用千问；模型可用性从实际业务调用和失败统计获取。首轮不加多应用worker或观测集群；保留必要反压/超时/有限重试和短任务峰值余量，参数不变成永久企业配额。

## 顺序特性

1. **标准观测接入：** SDK/Collector输出、两MCP跨进程关联、图/Saver观测与指标口径。通过真实固定动作和原业务路径核对，先不建设前端或同时启完整平台。
2. **观测平台集成：** 固定版本的Collector/Tempo/Prometheus/Grafana模板、真实存储/查询/仪表盘/深链接、重启/平台故障与实际资源证据。在明确隔离验证环境只增量启动观测组件；若上现有服务器先只读核对容量，不迁移DeepHelp应用或改变已有中间件。M23再交付完整远端学习环境。

同M一个PLAN，按序一个分支/特性；不能将纸面compose配置或界面截图称为集成通过。

## 验收

离线固定动作覆盖span关联、并发/取消、脱敏、隔离、统计分母、时钟、丢事件/导出失败与重放计数。实际MCP/实库固定动作证明caller/server/DB同链；固定审批动作证明interrupt→跨进程恢复→checkpoint→UNKNOWN/终态与Span Link，不让每个故障再经过模型。Grafana能检索同一trace，Prometheus能读到实际计数/直方图，独立重启平台后记录可读，关闭Collector/后端后业务不重复执行。

保留同一实际观测作为页面/报告依据。若业务请求/解析/决策未变，纯观测不为凑完整性新增模型调用；依赖M20内容证据须明确版本与目标适用。若改变模型请求或工具上下文协议，按影响增加必要内容实测。观测span和确定性协议通过不宣称新的模型质量通过。

退出时可按一个run重建实际时间线、查询当前身份的历史观测与指标，并能在专业平台关联同一请求，解释耗时/证据/缺失项。交付实际框架版本、管道/服务图、资源/持久与故障证据，不能声称本轮规划已安装框架。完成当前特性后停止，不自动建设前端、部署Langfuse或开始完整评测。

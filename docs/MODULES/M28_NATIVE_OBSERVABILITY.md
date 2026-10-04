# M28｜原生 Langfuse 观测与学习工作台

**状态：** 全部特性已交付。原生关联、固定实际业务内容、脱敏IO/usage、原工作台与故障重放已验证，证据及范围见[交接](../../handoffs/M28.md)。旧服务切换/退役另属M30。

**前置：** M16/M21/M22 的受保护 trace 查询及三视图、M20 两 MCP、M26 官方 saver 与 M27 实际平台。实施计划已由本规格/最终交接覆盖，历史从Git追溯。

## 接入方式的决定

主方案为 Langfuse 原生 Python SDK：显式 `start_as_current_observation`/受控 observe 接缝覆盖自定义主链；现有真实 LangGraph 审批部分使用官方 `langfuse.langchain.CallbackHandler`，在根 observation 下调用。每个并发请求单独管理 callback 状态；不复用可变 last_trace_id 作为归属依据。

当前模型网关是自建 httpx 异步请求，并非 OpenAI SDK。保留原预算、attempt、超时/取消、schema/tool/Embedding 解析与实际usage，在网关实际发送边界记录 generation；不为接观测替换请求库。OpenAI drop-in wrapper、全框架自动patch、LiteLLM等网关不加入主路线，以免重复 generation/费用或漏掉业务/工具/检索。

Langfuse SDK 内部仍基于 OpenTelemetry。新运行保留它需要的 OTel 依赖及 W3C 上下文；退役目标是自建独立 tracer/exporter/Collector→Tempo 链路，不是强行删除所有名为 opentelemetry 的包。M30 才停用旧容器。

## 复用已有观测价值

沿用 13 段和 run/question/operation/model/attempt/call/receipt/version/signature 标识；覆盖规则/实体/归属、Dense/BM25/Hybrid、缓存/Memory、SOP/工具、两MCP双端校验、实际 PostgreSQL/Milvus及官方 saver 的诊断。一个实际模型 attempt对应一个用量记录；父级汇总和 graph callback 不重复计费。缺失 usage 标未知，估算与实际分开，reasoning 已含 output 时不再相加。

业务端保持原请求/权限/receipt和事实语义，只有观测接缝变化。脱敏后的模型输入/输出可用于 LLM 诊断，与结构性 span 分开；不自动把所有函数参数/整个 graph state 作为观测内容。默认禁用 decorator 自动 IO 捕获，并核验 callback 的锁定脱敏/过滤能力。复用现有 Redactor/身份替代映射，隐藏推理、凭据、SQL参数、原始客户标识不采集。公开合成样本可有详细内容标记；不能因自托管就越过现有内容边界。

跨 MCP沿用独立的 deephelp/observation traceparent，不增加模型工具参数或传播身份凭据；新 SDK观察父子关系需实际验证，不假设设置 baggage 就完成 MCP 传播。等待/恢复分别结束/新建 trace；原 run/operation/原pause与当前恢复关联要能在 API 和页面查回。先验证 Langfuse 锁定版是否暴露 Span Link；不支持时使用受保护、持久且可翻页的显式前序关联，不把恢复 trace 伪造为旧 span的长子调用。人工等待与实际运行耗时分开。

## 查询和页面

改现有 `/observability/runs/{run_id}` 后端适配及工作台链接，不新增平行前端。先通过现有身份/session/run/审批归属校验，再用已登记观测ID查 Langfuse；客户端不提供任意 trace/search/project。保持分页、大小/时限边界、no-store、unavailable/truncated/incomplete 和外主体404；API密钥只在服务器。

普通工作台呈现当前授权 run 的受控投影，管理员 Langfuse 入口作为私有独立登录；不能用全局 Langfuse UI取代原应用授权。旧 Tempo历史保存在旧环境/受保护归档，新路线标明旧数据源或不可用，不伪造导入成当前原生 trace。业务历史依旧从 PostgreSQL账本读取。

## 验收与停止

首试验模型0次，用固定动作证明应用→两MCP跨进程、官方图/保存器、pause→新进程resume可关联、API分页、内容脱敏及观测关闭；失败先修接缝，不批量重写所有 decorators。后续通过一个代表客诉检索路径及一个工具选择/回复路径，按实际影响选择必要真实模型内容；固定故障/审批不重复经过模型。

实际 Langfuse 页面读回输入输出/模型/usage/版本、流程与检索来源；重复 receipt不产生新 generation，导出失败不重复业务、无限重试或拖住取消。原三视图/学习页/越权守卫及业务效果计数回归通过后才具备退役旧管线条件。“完成M28”在关联首试验验收交付后继续业务全覆盖与工作台替换，无需用户另启；本M全部特性交付后停止。

依据：[原生 SDK](https://langfuse.com/docs/observability/sdk/instrumentation)、[LangChain/LangGraph 回调](https://langfuse.com/integrations/frameworks/langchain)。接入方式不同于传输标准，不能把 SDK 与 OTel 视为互斥选项。

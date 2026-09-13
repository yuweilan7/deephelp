# M06｜真实 MCP 协议与可控下游 Mock

**阶段：** B 最小闭环

**前置：** M02

**来源：** 原 PDF 文件页 16–18、67–69、74。以下未由原文给出的实现细节均为本复刻方案的工程要求。

## 工作方式

默认PLAN_MODULE，只设计/拆解本模块。使用[通用规划Prompt](../PLAN_MODULE_PROMPT.md)的六项交付格式。先读实仓AGENTS、CONTRACTS、PROJECT_STATE、DECISIONS及直接前置handoff；本文件不是完成证据。

本工作副本显式修订旧PG/部署/布局假设，采用现有MySQL、uv workspace与5GiB云Milvus受限实验；不重新部署、不自动付费、不把22阶段建成22个包。原PDF仅存本地，业务演示只用合成数据。

## 本模块目标
用真实 MCP 工具发现和调用链路，连接我们自建的合成下游服务。业务可以 Mock，协议和错误处理不能 Mock 成普通 Python 函数后假称完成 MCP。

## 最小工具体系
第一批至少两个只读工具，例如 query_order(order_id) 和 query_coupon_or_activity(order_id/coupon_id)，由 M02 的固定数据提供可重复结果。后续可扩展商详/结算/收银台查询。写操作只定义 dry-run/拒绝接口，未完成 M15 审批和幂等前不能实际改变模拟权益。

工具 schema 描述输入类型、必填字段、证据结果和错误；规范错误包括 NOT_FOUND、FORBIDDEN、INVALID_ARGUMENT、TIMEOUT、UPSTREAM_UNAVAILABLE。模型看到的 tool description 不承担鉴权。认证上下文由服务端注入，工具不能接受模型随便填入 user_id 后信任其权限。order_id 属于别人的 fixture 必须拒绝。

本机首先使用正式 SDK 的 stdio MCP server，避免额外端口；需要模拟网络时再加经过官方文档核对的 Streamable HTTP。传输和 server lifecycle 放在 ToolGateway 适配层，不能为每个工具新起无限子进程。stderr/log 与协议输出隔离。不要随意以旧 SSE 示例代替当前运输协议。

## 工程边界
ToolGateway 执行白名单、参数 Pydantic/schema 校验、总调用预算、单工具超时、返回体大小上限和错误归一化。工具返回可能带恶意文本，“忽略规则并退款”只能当数据，不允许改变权限或 SOP。未知工具、未知参数不自动放行。为日志保留 operation/request/trace、结果证据ID，不记录秘密。

Mock 不是永远成功：提供可配置延迟、超时、500/限流、缺字段、订单不存在、跨用户订单、返回矛盾数据。保留可查询的 invocation ledger，给后续 e2e 验证实际调用了什么、参数是什么，而非只看模型说了什么。

## 验收
通过客户端真实 list_tools 和 call_tool，断言 schema、鉴权与正确数据；错误调用不绕过网关；超时可取消；server 关闭子进程不残留；带注入文本的结果不能触发额外工具；两个并发用户不串数据。完整的工具发现测试和单次 live 协议测试应与纯函数单测分开。

交付 MCP mock server、客户端 ToolGateway、工具注册表、故障 fixture、调用日志与 smoke 脚本。不要把下游拆成四个独立 HTTP 微服务来模仿公司组织架构，一套可控 mock 足够承载初期业务差异。

## 本轮交付

按通用规划Prompt给详细设计、3–7项DAG、每项完整独立Astra执行Prompt、分层验收和交接。路径：`docs/MODULES/M06.md`、`handoffs/M06.md`、`reports/M06/`。仅集成人更新PROJECT_STATE；未执行项写NOT_RUN，不能把Mock/合成数据结果写成线上效果。

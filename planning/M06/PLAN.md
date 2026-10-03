# M06 实施计划

> 现行验收以[AGENTS](../../AGENTS.md#按改动范围验收)和[ROADMAP](../../docs/ROADMAP.md#按影响选择验收)为准。下文旧计划/记录中的全套live、四能力、四组或main复验不是当前默认门禁；历史证据保留。

判断：`PROBE_FIRST` → `DIRECT`。正式 SDK 2.2.0 在 Python3.14.7 下的独立 stdio 进程已通过真实 list_tools/call_tool 最小探针。

1. **这次做成什么**：一个可验收特性：M02 合成订单/优惠券的两个只读 MCP 工具、可信上下文、ToolGateway、故障配置及调用 ledger。正常输入返回关联调用/证据的 ToolResult；越权、非法参数、超时与坏结果明确拒绝。范围不含模型、云业务库、SOP、写工具或 M07。
2. **从哪里改**：复用唯一 ToolRequest/ToolResult、M02 fixture/归属守卫、ExecutionBudget；新增 MCP 注册表、stdio server/gateway/smoke 及测试。新增 NOT_FOUND 错误及仅被本模块消费的内部信封/ledger DTO；更新应用依赖与单一根锁，不改数据库。
3. **怎样证明**：纯校验单测与真实 stdio 集成分开；验证 schema、正确金额/状态、双用户/租户、正文冒充身份、无签名/篡改/重放、预算/白名单、模拟500/限流/缺字段/矛盾/注入/体积、取消后 ledger 和正常再调用、退出无残留。独立 smoke 的 feature 完整验收与 main 最小复验写 .local；两分支运行 ROADMAP 的 Ruff、格式、mypy、pytest、diff 检查。
4. **什么时候停**：单工具/启动/总运行均有 timeout，共享有限调用预算和有限 ledger，默认重试0；不可恢复的 SDK/协议故障、检查或 Git 同步失败停止并报告。目标为本机自建合成服务，无账户消费和云维护。
5. **怎样交付**：`feature/m06-mcp-readonly`，一个主写者。内容与协议通过后更新 CONTRACTS/运行入口、handoffs/M06.md 和 PROJECT_STATE，按 AGENTS 提交、推送、合并、复验、双分支同步；保留特性分支，回到干净 main。

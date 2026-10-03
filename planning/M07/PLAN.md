# M07 实施计划

> 现行验收以[AGENTS](../../AGENTS.md#按改动范围验收)和[ROADMAP](../../docs/ROADMAP.md#按影响选择验收)为准。下文旧计划/记录中的全套live、四能力、四组或main复验不是当前默认门禁；历史证据保留。

判断：`PROBE_FIRST`。一个特性，基线 `d45fad016e17ee51b450d71aa07c80e7a313c2f7`。

1. **这次做成什么**：已确定意图和带来源槽位进入三份版本化 SOP，模型在白名单内选择只读工具/结束，代码按证据分支返回 SOPResult。缺资料不调用模型/工具，失败和循环有界。converse、意图分类、润色、持久审批不在本轮。
2. **从哪里改**：复用 M02 Question/SOPResult、M03 ChatPort、M06 ToolPort/真实 stdio；新增 SOP JSON schema、三配置、版本化 Prompt、执行器/端口和演示/验收入口。失败调用 ID 也需留在 SOPResult，证据只能引用其中成功调用；兼容回归现有消费者。无新依赖、锁变化或业务库迁移。
3. **怎样证明**：先用 qwen3.8-flash 原生工具选择和 M06 实际 ledger 检查可调用性。三个 SOP 各覆盖正常/缺资料/下游失败；固定模型回放验证重复、未知/写工具、换订单/身份、越权指令、注入、无证据、版本、共享预算、单次/总时限、重试和取消。真实探针核对结构化事实、证据和调用 ledger，合并后复验。根执行 ROADMAP 的 Ruff 检查/格式、mypy、pytest、git diff --check；入口见应用 README。
4. **什么时候停**：只用现有千问候选和本机合成 MCP，不访问云业务库/P00。运行设置共享调用/token/费用上限及超时，累计状态留 `.local/m07`，恢复不归零。真实鉴权/网络/服务不可恢复、没有可用合适模型、Git失败时停止；正常响应后的解析/断言问题先保存脱敏结构并修复复验。参数和模型选择按 AGENTS 自行落实。
5. **怎样交付**：`feature/m07-bounded-sop`；全部验收后中文提交/push、合并 main、规定复验、两分支与远端 SHA 同步，最后回干净 main。更新 CONTRACTS、M07 handoff、PROJECT_STATE 及运行入口。M08 等待用户另行启动。

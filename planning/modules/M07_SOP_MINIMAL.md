# M07｜最小可配置 SOP 与有界 ReAct 执行器

**阶段：** B 最小闭环

**前置：** M02, M03, M06

**来源：** 原 PDF 文件页 67–69、73。以下未由原文给出的实现细节均为本复刻方案的工程要求。

## 工作方式

默认PLAN_MODULE，只设计/拆解本模块。使用[通用规划Prompt](../PLAN_MODULE_PROMPT.md)的六项交付格式。先读实仓AGENTS、CONTRACTS、PROJECT_STATE、DECISIONS及直接前置handoff；本文件不是完成证据。

本工作副本显式修订旧PG/部署/布局假设，采用现有MySQL、uv workspace与5GiB云Milvus受限实验；不重新部署、不自动付费、不把22阶段建成22个包。原PDF仅存本地，业务演示只用合成数据。

## 本模块目标
实现能读取 SOP、调用工具、根据结果分支并产出结构化结论的最小执行器。不要退化成“模型自由聊天+几个工具”，也不要在只有三个流程时创造完整 BPMN 引擎。

## 最小 SOP 规格
编写三份版本化 SOP：优惠未享受原因排查、优惠券不可用排查、订单参加活动查询。每份明确适用 intent_code、required_slots、allowed_tools、步骤与分支、停止条件、人工接管条件、输出 evidence 要求。使用 YAML/JSON 加静态 schema；条件是受限表达式或明确字段判断，不运行来自模型/配置的任意 Python eval。

SOPExecutorPort 输入已确定意图、实体、业务上下文、授权和预算；输出 SOPResult(status,facts,evidence_refs,tool_call_ids,next_action)。执行器不能再把用户原文送回另一个主意图识别器。前置槽位不足返回 WAITING_SLOT，不能让模型猜订单。工具输出无结论依据时返回无法确认/人工处理，不能靠推理补写成功。

ReAct 可用于有约束地选择下一个获准工具、解释观察，但必须有 max_steps、总时限、最大工具次数、重复调用检测、单次失败与总重试上限。确定性规则负责权限、参数、终止和高风险拦截；模型只能在允许边界内做选择。Prompt 文件版本化，区分可信 system/SOP 与不可信 user/tool 文本。

## 本轮实现边界
优先 LangGraph 中一个独立 SOP 子图或等价受控执行单元，使用其余已有网关。不引入复杂多 Agent 主从结构。M15 前仅允许只读工具，不实现“临时自动批准”占位。先返回 JSON 事实，前端只显示 JSON/简易文字。REPLY_POLISH 不在本模块负责。

## 验收
三个 SOP 至少各有正常、资料不足、下游失败三条路径；模型重复同一工具被截断；编造工具名被拒；用户要求越过规则被拒；工具返回包含恶意命令不能更改工具白名单；不满足证据条件不能标记已解决。用调用 ledger 验证实际行为，不把最终自然语言“已查询”当调用证据。

给出一次真实模型+真实 MCP Mock 的受控执行探针，其它路径用可重放模型 fixture 保持测试稳定。交付 SOP schema/三个配置/执行器接口/Prompt 版本/循环预算测试。未来接 AgentScope 只替换 SOPExecutorPort，不改变业务模型。

## 本轮交付

按通用规划Prompt给详细设计、3–7项DAG、每项完整独立Astra执行Prompt、分层验收和交接。路径：`docs/MODULES/M07.md`、`handoffs/M07.md`、`reports/M07/`。仅集成人更新PROJECT_STATE；未执行项写NOT_RUN，不能把Mock/合成数据结果写成线上效果。

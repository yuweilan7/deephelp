# 09｜LLM、Agent、tool、SOP与LangGraph

[上一章](08-message-path.md) · [课程入口](README.md)

## 业务问题与目标

模型建议“查订单”，谁决定能不能查、查哪个、何时结束？目标是拆开语言判断和确定性执行，理解受控Agent回合。前置为08章的槽位、事实和计数。

## 一次Agent回合

LLM接收消息/工具描述/schema，返回文本、结构值或tool_calls，本身没有PG访问能力。Agent在模型与工具之间迭代：给上下文→取建议→校验→执行工具→把结果放入下轮上下文→达到终止条件。SOP限定步骤、工具、必填字段、分支和上限；治理版固定快照和节点路径。

tool_call是建议值，ToolGateway负责正式MCP协议、鉴权/schema和结果。模型说成功不构成订单事实；finish_sop也是控制建议，最终结论由事实规则评估。

LangGraph是状态图编排库，当前Conversation是Python顺序编排，审批子流程才使用实际图、interrupt/resume和官方PG saver。13段不是13个图节点，checkpoint不是业务账本。Java状态机可类比节点/边，但暂停状态、恢复参数和副作用仍需单独守卫。

## 实验与预期

离线、合成、远程模型0次；可读01/08章同一次输出：

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course request
```

answered为model_calls=2、tool_calls=1：RecordedModel建议查订单，获得合成事实后建议finish_sop。stdio协议是真实本机SDK，建议是固定回放，不能证明LLM理解物流或企业泛化。missing/unknown工具0次。

## 按回合读源码

打开[SOPExecutor._loop](../../modules/deephelp-app/src/deephelp_app/application/sop.py)：

1. values从已验证entities建立；expected挑required_slots，模型不能猜未知订单。
2. ModelTool列白名单/schema，增加finish_sop/handoff_sop。
3. await model.chat等待模型端口，共享预算限制回合、重试、工具步数/deadline。
4. 校验建议数量、名称、arguments后才await tools.execute；失败明确停住。
5. observation进入后续消息；finish检查已有事实，不能先凭空结束。

[ReplyComposer](../../modules/deephelp-app/src/deephelp_app/application/reply.py)先生成模板，可选模型只选opening/closing枚举。Java策略接口可类比替换实现；模型输出仍是外部不可信输入。

## 常见误区与反例

- Prompt不能强制权限/幂等/金额规则，代码与账本负责。
- 更强模型不能恢复未召回资料或绕过缺槽。
- 工具名相同不等于结果可信，两端schema/身份及事实证据闭合都要校验。

## 练习与答案

预测模型建议delete_order；或建议query_order但带另一个用户的订单。

答案：前者白名单拒绝，后者参数/归属/结果守卫拒绝。普通消息不能批准旧退款建议，必须走显式绑定命令、唯一领取；后续审批章继续解释。

## 受控专业 Agent委托（M40）

普通 SOP 的模型在同一上下文选择当前工具；协作路径增加一个协调角色和一个拥有独立任务上下文的订单权益角色。协调者先建议 `delegate_task`，专业角色选择 `get_order_benefits`，观察成功后用 `return_evidence` 交还实际证据 ID。它有自己的指令、目标、工具许可和结构化回执；原600确定意图，原SOP继续分支/比较并生成事实回复。

```powershell
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course collaboration
```

预期：`remote_model_calls=0`、`recorded_decisions=3`、`real_stdio_calls=1`；原SOP路径为 `order → decide → applied`，回执 `COMPLETED`，实付 `99.90`、优惠 `10.00`。固定动作只证明机制，实际模型内容另由显式live入口验证。本机stdio SDK与原工具观察真实运行，退出实验后关闭子进程。

按顺序读 [AgentTask/AgentReceipt](../../modules/deephelp-app/src/deephelp_app/domain/models/collaboration.py)、[CollaborationSession.lookup](../../modules/deephelp-app/src/deephelp_app/application/collaboration.py)、[SOPExecutor._flow](../../modules/deephelp-app/src/deephelp_app/application/sop.py)。任务固定父run/question、身份/session、问题输入版本、实体原来源、角色/策略摘要、工具许可和预算引用。正文不能填写这些可信字段。模型返回另一订单、未知角色或假证据均在接收处拒绝；不同来源矛盾仍按原SOP移交，不能让另一个Agent投票创造事实。

`execute`拥有本次 `CollaborationSession`，两个请求没有共享任务列表。`lookup`的await沿当前Task顺序等待模型与原工具，不创建游离后台任务；原ToolGateway拥有协议连接/派发记录和取消释放。所有角色借用同一个 `ExecutionBudget`，模型网关/工具网关分别预留，协调用例不能重复结算。模型超时的预留仍未知，不退款成新额度。

Java可类比一个用例调用受权限约束的策略对象并收到不可变结果，但LLM建议是外部不可信输入，不能等同于已审核的Java代码。Python `await`让出当前事件循环，并非每个角色自动拥有独立线程；这里选择顺序委托，是因为后续业务分支必须先有当前查询证据。

工作台明确选择协作后发送新消息；“事实/时间线”和“工具与工作流”沿所选原run展示协调者、专业角色、槽位来源、实际模型动作和call/证据ID。默认单Agent，读取历史0模型；缺槽先补充，未记录任务不补造协作故事。重投同message_id保留原执行模式，不能借重投把single改成collaboration。

练习：专业角色返回 `COMPLETED` 却引用不存在的证据；或任务来自另一个question但订单号相同；或用同message_id改执行模式。答案：第一种回收校验拒绝，原SOP不能回答成功；第二种父链/归属守卫拒绝，编号相同不提供授权；第三种原消息hash冲突，旧single默认hash保持兼容。若用户缺订单号，预期没有委托、没有工具，原主链先等待补充。

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

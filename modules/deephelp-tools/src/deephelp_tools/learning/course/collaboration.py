"""用固定模型动作与真实本机 MCP 运行原委托核心，默认离线/远程模型0次。

RecordedAgentModel 是明确合成的建议端口，观察仍来自原工具与事实守卫。
读 collaboration_demo 后进入应用 CollaborationSession.lookup，不另造教学引擎。
"""

import json
from typing import Any, cast

from deephelp_app.application.collaboration import load_agent_policy
from deephelp_app.application.sop import SOPExecutor
from deephelp_app.application.sop_governance import bundled_registry
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.intents import IntentCode
from deephelp_app.domain.models.model_io import ChatRequest, ChatResult, ModelToolCall, ModelUsage
from deephelp_app.domain.models.sop import SOPStatus
from deephelp_tools.demo.assembly import DemoToolGateway
from deephelp_tools.probes.conversation.sop_probe import sop_question


class RecordedAgentModel:
    """只提供固定合法建议；用于机制实验，不能作为真实模型内容验收。"""

    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    async def chat(self, request: ChatRequest, budget: ExecutionBudget) -> ChatResult:
        budget.claim_attempt(retry=False)
        self.requests.append(request)
        tool = request.tools[0]
        if tool.name in {"get_order_benefits", "check_coupon"}:
            assert request.messages[0].content is not None
            task = json.loads(request.messages[0].content.split("TRUSTED TASK:\n", 1)[1])
            arguments = {"order_id": task["parameters"]["order_id"]}
            if tool.name == "check_coupon":
                arguments["coupon_id"] = task["parameters"]["coupon_id"]
        else:
            properties = cast(dict[str, Any], tool.parameters["properties"])
            arguments = {
                key: value["items"]["enum"] if value["type"] == "array" else value["enum"][0]
                for key, value in properties.items()
            }
        return ChatResult(
            tool_calls=[
                ModelToolCall(
                    call_id=f"recorded-agent-{len(self.requests)}",
                    name=tool.name,
                    arguments=arguments,
                )
            ],
            usage=ModelUsage(),
            model="recorded-offline-agent",
            finish_reason="tool_calls",
        )


async def collaboration_demo() -> dict[str, object]:
    """依次运行订单/券门槛两例，复用同核心；每例上下文负责退出MCP子进程。"""
    cases = []
    for name, intent, order, coupon in [
        ("order", IntentCode.DISCOUNT_MISSING, "000031", None),
        ("coupon-threshold", IntentCode.COUPON_UNUSABLE, "DEMO-C01", "COUPON-C01"),
    ]:
        budget = ExecutionBudget.start(30, 12, 0)
        model, gateway = RecordedAgentModel(), DemoToolGateway()
        executor = SOPExecutor(
            model, gateway, registry=bundled_registry(), agent_policy=load_agent_policy()
        )
        question, context = sop_question(budget, intent, order=order, coupon=coupon)
        question = question.model_copy(
            update={"versions": executor.pin_versions(question.versions, intent)}
        )
        context = context.model_copy(update={"execution_mode": "collaboration"})
        async with gateway.open():
            result = await executor.execute(
                question, budget, context=context, run_id="course-" + name
            )
            ledger = await gateway.ledger()
        assert result.status == SOPStatus.RESOLVED
        assert len(result.agent_receipts) == len(ledger) == (2 if coupon else 1)
        cases.append(
            {
                "case": name,
                "recorded_decisions": len(model.requests),
                "real_stdio_calls": len(ledger),
                "result": result.model_dump(mode="json"),
                "shared_budget": budget.usage().model_dump(mode="json"),
            }
        )
    return {"evidence_kind": "synthetic_mechanism_replay", "remote_model_calls": 0, "cases": cases}

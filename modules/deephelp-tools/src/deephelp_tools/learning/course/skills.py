"""Skill学习入口：固定建议→原SOP→版本化Skill→真实stdio事实，远程模型0次。

固定建议只模拟ChatPort，工具/输入/证据仍走应用核心；gateway上下文负责关闭子进程。
"""

import json

from deephelp_app.application.sop import SOPExecutor
from deephelp_app.application.sop_governance import bundled_registry
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.intents import IntentCode
from deephelp_app.domain.models.model_io import ChatRequest, ChatResult, ModelToolCall, ModelUsage
from deephelp_tools.demo.assembly import DemoToolGateway
from deephelp_tools.probes.conversation.sop_probe import sop_question


class RecordedSkillModel:
    """明确离线的合法原生工具建议，不将其记为真实模型质量。"""

    async def chat(self, request: ChatRequest, budget: ExecutionBudget) -> ChatResult:
        budget.claim_attempt(retry=False)
        assert request.messages[-1].content is not None
        ready = json.loads(request.messages[-1].content.split("\nInvoke exactly", 1)[0])
        return ChatResult(
            tool_calls=[
                ModelToolCall(
                    call_id="recorded-" + ready["ready_node"],
                    name=ready["required_action"],
                    arguments=ready["verified_parameters"],
                )
            ],
            usage=ModelUsage(),
            model="recorded-offline-skill",
            finish_reason="tool_calls",
        )


async def skills_demo() -> dict[str, object]:
    """原核心订单例，核对回执、实际工具账本与金额；离开async with才交付结果。"""
    cases = []
    for name, intent, order, coupon in [
        ("order", IntentCode.DISCOUNT_MISSING, "000031", None),
        ("order-coupon", IntentCode.COUPON_UNUSABLE, "DEMO-C01", "COUPON-C01"),
    ]:
        budget = ExecutionBudget.start(30, 8, 0)
        gateway = DemoToolGateway()
        executor = SOPExecutor(RecordedSkillModel(), gateway, registry=bundled_registry())
        question, context = sop_question(budget, intent, order=order, coupon=coupon)
        question = question.model_copy(
            update={"versions": executor.pin_versions(question.versions, intent)}
        )
        async with gateway.open():
            result = await executor.execute(
                question, budget, context=context, run_id="course-skill-" + name
            )
            ledger = await gateway.ledger()
        assert result.status == "RESOLVED" and len(ledger) == (2 if coupon else 1)
        leaves = [r for r in result.skill_receipts if r.observation]
        assert [r.observation.call_id for r in leaves if r.observation] == [
            r.call_id for r in ledger
        ]
        cases.append(
            {
                "case": name,
                "real_stdio_calls": len(ledger),
                "result": result.model_dump(mode="json"),
                "budget": budget.usage().model_dump(mode="json"),
            }
        )
    return {"remote_model_calls": 0, "cases": cases}

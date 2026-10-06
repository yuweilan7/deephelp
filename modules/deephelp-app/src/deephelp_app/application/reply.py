"""把经过校验的SOP事实渲染为回复，供主链终态之前调用。

fact_reply从Fact/Money生成金额与结论；可选语气模型只选择固定开头/结尾标识。
compose返回正文和ReplyPresentation，后者解释模板、润色或降级的实际来源。
可选润色失败保留原事实模板，调用者取消仍按取消处理。
"""

import asyncio
from typing import Literal

from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import DTO, ErrorCode, Money
from deephelp_app.domain.models.model_io import ChatMessage, ChatRequest
from deephelp_app.domain.models.responses import ReplyPresentation
from deephelp_app.domain.models.sop import SOPResult, SOPStatus
from deephelp_app.domain.ports import ChatPort


class Wording(DTO):
    opening: Literal["none", "checked"]
    closing: Literal["none", "help"]


OPENINGS = {"none": "", "checked": "本次核查结果如下。"}
CLOSINGS = {"none": "", "help": "如需进一步核实，可联系人工。"}


def fact_reply(result: SOPResult, *, continuation: bool = False) -> str:
    # 数字从校验后的Fact/Money渲染；语气模型没有自由编写事实的入口。
    if result.status == SOPStatus.WAITING_SLOT:
        names = "、".join(s.value for s in result.missing_slots)
        return f"缺少或无法确认 {names}。" + (
            "请带本问题编号补充或更正；也可以发送完整的新问题。"
            if continuation
            else "请用一条新消息重新发送完整问题及这些编号；当前不自动合并跨消息槽位。"
        )
    if result.status == SOPStatus.FAILED:
        return "本次查询未取得可验证结论，请稍后重试或联系人工。"
    facts = {f.name: f.value for f in result.facts}
    parts = [f"订单 {facts['order_id']}。"] if "order_id" in facts else []
    for key, label in (("paid", "实付"), ("discount", "已记录优惠")):
        value = facts.get(key)
        if isinstance(value, Money):
            parts.append(f"{label} {value.amount:.2f} {value.currency}。")
    if "coupon_id" in facts and "coupon_status" in facts:
        parts.append(f"券 {facts['coupon_id']}，状态 {facts.get('coupon_status')}。")
    if "attempt_id" in facts:
        parts.append(
            f"下单尝试 {facts['attempt_id']}，状态 {facts['attempt_status']}。"
            f"原因代码：{facts['failure_code']}；记录原因：{facts['failure_reason']}；"
            f"记录的后续指引：{facts['follow_up']}。"
        )
    if "refund_id" in facts:
        parts.append(
            f"退款 {facts['refund_id']}，状态 {facts['refund_status']}；"
            f"券回退状态 {facts['return_status']}。核对依据：{facts['return_reason']}。"
        )
    if "activity_ids" in facts:
        parts.append(f"活动：{facts['activity_ids'] or '未查询到活动记录'}。")
    if "eligibility" in facts:
        parts.append(
            f"活动 {facts['activity_id']}（{facts['activity_label']}），"
            f"资格状态 {facts['eligibility']}，剩余名额 {facts['remaining_slots']}。"
        )
        parts.append(f"规则：{facts['activity_rules']}；资格记录：{facts['eligibility_reason']}。")
    if "sop_conclusion" in facts:
        parts.append(str(facts["sop_conclusion"]).rstrip("。.!?！？") + "。")
    if result.status == SOPStatus.NEEDS_APPROVAL:
        parts.append("仅形成待人工确认的建议，尚未申请、批准或执行变更。")
    elif result.status == SOPStatus.HANDED_OFF:
        parts.append("需要人工进一步核实。")
    return "".join(parts)


def apply_wording(template: str, proposal: object) -> str:
    wording = Wording.model_validate(proposal)
    return OPENINGS[wording.opening] + template + CLOSINGS[wording.closing]


class ReplyComposer:
    def __init__(self, model: ChatPort | None = None, *, timeout: float = 10) -> None:
        self.model, self.timeout = model, timeout

    async def compose(
        self, result: SOPResult, budget: ExecutionBudget, *, continuation: bool = False
    ) -> tuple[str, ReplyPresentation]:
        """返回事实正文与展示来源；可选润色只选择固定语气标识。

        continuation决定缺槽提示，不能替换问题实体；budget仍是原请求预算。
        语气失败可降级模板，取消不转换成成功回复。
        """
        template = fact_reply(result, continuation=continuation)
        if self.model is None or result.status not in {SOPStatus.RESOLVED, SOPStatus.HANDED_OFF}:
            return template, ReplyPresentation(template=template)
        try:
            # Leave room for finalization. Optional wording cannot consume the whole deadline.
            remaining = budget.remaining_seconds() - 1
            if remaining <= 0:
                return template, ReplyPresentation(
                    template=template, mode="fallback", reason="deadline"
                )
            async with asyncio.timeout(min(self.timeout, remaining)):
                answer = await self.model.chat(
                    ChatRequest(
                        messages=[
                            ChatMessage(
                                role="system",
                                content="选择固定语气标识。只能输出opening=none/checked及closing=none/help；"
                                "禁止生成事实、数字、动作或回复正文。",
                            ),
                            ChatMessage(
                                role="user", content=f"查询结果状态：{result.status.value}"
                            ),
                        ],
                        response_format="json_schema",
                        output_schema=Wording.model_json_schema(),
                        max_output_tokens=2048,
                    ),
                    budget,
                )
                reply = apply_wording(template, answer.structured)
            return reply, ReplyPresentation(template=template, mode="polished", reason="accepted")
        except TimeoutError:
            reason: Literal["invalid_output", "unavailable", "deadline"] = "deadline"
        except ValueError:
            reason = "invalid_output"
        except AppError as exc:
            reason = (
                "invalid_output" if exc.code == ErrorCode.MODEL_OUTPUT_INVALID else "unavailable"
            )
        except Exception:
            reason = "unavailable"
        # CancelledError remains cancellation; never turn it into a successful reply.
        return template, ReplyPresentation(template=template, mode="fallback", reason=reason)

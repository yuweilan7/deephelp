"""从原receipt和可选诊断生成确定性运行解释，供授权历史API调用。

先读账本事实再解释诊断；未终态、缺快照和漏观测按原状态展示。
explain_run不重投消息、不调用模型，解释句只来自已记录的结构。
"""

from typing import Any

from deephelp_app.domain.history import HistoryMessage
from deephelp_app.domain.models.responses import DebugSnapshot

STEPS = {
    "100": ("接收与校验", "确认本次消息及请求边界"),
    "200": ("安全检查", "核对主体与安全边界"),
    "300": ("文本与实体", "从原文识别字段、否定和更正"),
    "400": ("读取记忆", "读取同一主体会话的上下文窗口"),
    "500": ("事件归属", "判断补充属于哪一件事，歧义时等待澄清"),
    "600": ("识别意图", "沿已记录的规则、检索及模型级联选择"),
    "700": ("检查意图", "核对可执行意图与缺失槽位"),
    "800": ("检查关键词", "按原记录核对关键词检查状态"),
    "900": ("选择流程", "按已绑定意图选择固定 SOP"),
    "1000": ("执行工具", "校验动作与参数并读取下游事实"),
    "1100": ("形成回复", "由已验证事实与证据生成结果"),
    "1200": ("保存终态", "提交原回执、问题与 outbox"),
    "1300": ("结束请求", "记录返回；投影完成仍须独立观察"),
}

OUTCOMES = {
    "ANSWERED": "已验证的事实支持这次回答。",
    "CLARIFY": "等待补充或澄清；不能据此声称已查询业务。",
    "HANDOFF": "本次转交处理；相似候选不等于可执行意图。",
    "REJECTED": "请求被拒绝；以原错误与动作记录核对边界。",
    "ERROR": "本次执行失败；已发送的动作是否产生效果须查原操作账本。",
    "PENDING_APPROVAL": "原计划等待明确审批；读取不会批准或恢复。",
}


def explain_run(
    row: HistoryMessage, snapshot: DebugSnapshot | None, *, dropped: int = 0
) -> dict[str, Any]:
    receipt = row.response
    data = snapshot.data if snapshot else {}
    gaps = list(snapshot.incomplete) if snapshot else ["debug_snapshot_unavailable"]
    if dropped:
        gaps.append("diagnostic_events_dropped")
    stages = []
    for stage in receipt.stages if receipt else ():
        number = stage.stage.split("_", 1)[0]
        title, purpose = STEPS.get(number, (stage.stage, "该步骤名称未登记；只展示原记录"))
        stages.append(
            {
                **stage.model_dump(mode="json"),
                "title": title,
                "purpose": purpose,
                "source": "receipt.stages",
            }
        )
    decision = (
        receipt.intent_decision.model_dump(mode="json")
        if receipt and receipt.intent_decision
        else None
    )
    return {
        "schema_version": "run-explanation-v1",
        "run_id": row.run_id,
        "trace_id": receipt.trace_id if receipt else None,
        "diagnostic_trace_id": snapshot.trace_id if snapshot else None,
        "source": "postgresql_ledger",
        "read_model_calls": 0,
        "input": row.input.model_dump(mode="json"),
        "summary": OUTCOMES.get(receipt.outcome.value, "结果类型未登记，以原回执为准。")
        if receipt
        else "尚无终态回执；不能推断资源已关闭或下游没有效果，也不会自动重领。",
        "status": row.status.value,
        "receipt": receipt.model_dump(mode="json") if receipt else None,
        "question_now": row.question.model_dump(mode="json"),
        "question_at_run": data.get("question"),
        "input_at_run": data.get("input"),
        "entities": data.get("text"),
        "event": data.get("event"),
        "decision": decision,
        "stages": stages,
        "tools": data.get("tools"),
        "sop_nodes": data.get("sop_nodes"),
        "agent_receipts": [r.model_dump(mode="json") for r in receipt.agent_receipts]
        if receipt
        else None,
        "retrievals": data.get("retrievals"),
        "memory_activity": data.get("memory_activity"),
        "gaps": gaps,
        "boundaries": [
            "原消息/回执来自 PG；当前问题可能已被后续消息修改，原问题快照来自诊断。",
            "实体区间指向脱敏前原文；替代编号长度不能用来重算偏移。",
            "阶段耗时属于原执行，重叠时间不能相加；没有排队或 CPU 观测就保持未知。",
            "工具建议不是事实；原回执事实已通过 schema、归属与证据校验。",
            "浏览新增模型与工具调用均为 0；重投返回计数在客户端单独显示。",
        ],
        "sources": {
            "receipt": "dh_m08_runs.response",
            "input": "dh_m08_messages.body",
            "question_now": "dh_m08_questions.body",
            "diagnostics": "scoped debug-v1 snapshot",
        },
    }

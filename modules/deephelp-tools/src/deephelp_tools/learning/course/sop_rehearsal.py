"""作者预演实验复用草稿候选、原执行器和持久任务；网络由课程入口封住。"""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from deephelp_app.adapters.releases.sop_drafts import SOPDraftStore, draft_registry
from deephelp_app.application.sop_governance import bundled_registry
from deephelp_app.domain.models.common import VerifiedIdentity
from deephelp_app.domain.models.intents import IntentCode
from deephelp_tools.learning.replay.sop_rehearsal import execute_rehearsal


async def sop_rehearsal_demo() -> dict[str, object]:
    """相同候选的正常/缺槽/错误事实：比较真正结果，不把runner返回当PASS。"""
    owner = VerifiedIdentity(tenant_id="course", user_id="reader")
    base = bundled_registry()
    with TemporaryDirectory(prefix="deephelp-sop-rehearsal-") as directory:
        store = SOPDraftStore(Path(directory))
        draft = store.create(owner, base, base.definition(IntentCode.ORDER_ACTIVITY_QUERY).sop_id)
        registry = draft_registry(draft)
        cases: list[dict[str, Any]] = []
        for scenario, slots in [
            ("normal", {"order_id": "90234105"}),
            ("normal", {}),
            ("invalid_fact", {"order_id": "90234105"}),
        ]:
            result = await execute_rehearsal(
                dict(
                    registry=registry.model_dump(mode="json"),
                    intent_code="ORDER_ACTIVITY_QUERY",
                    owner=owner.model_dump(mode="json"),
                    run_id="course-rehearsal-" + str(len(cases)),
                    mode="offline",
                    scenario=scenario,
                    slots=slots,
                )
            )
            cases.append(result)
        return {"remote_model_calls": 0, "business_writes": 0, "cases": cases}

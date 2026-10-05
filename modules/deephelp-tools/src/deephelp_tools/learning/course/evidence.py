"""用实际脱敏/投影和冻结审计/门禁解释证据，默认无平台调用。"""

import json
from copy import deepcopy
from typing import Any

from deephelp_app.application.debug import DebugView, project, sanitize, scope_hash
from deephelp_app.domain.models.common import BudgetUsed, VerifiedIdentity
from deephelp_app.domain.models.responses import DebugSnapshot
from deephelp_tools.evaluation.quality.core import audit, ratio, targeted_gate


async def observations_demo() -> dict[str, object]:
    identity = VerifiedIdentity(tenant_id="synthetic-tenant", user_id="synthetic-user-a")
    key = b"course-only-synthetic-key-32bytes!"
    data = sanitize(
        {
            "input": {"raw_text": "订单000031优惠没到账", "order_id": "000031"},
            "response": {"reply": "订单000031实付99.90 CNY"},
            "budget": BudgetUsed(tokens=None, token_upper_bound=2000).model_dump(),
            "api_key": "SYNTHETIC_SECRET",
            "reasoning_content": "SYNTHETIC_HIDDEN",
            "versions": {"model": "synthetic-replay"},
        },
        key,
    )
    snapshot = DebugSnapshot(
        scope_hash=scope_hash(key, identity, "course-observation"),
        run_id="synthetic-run",
        question_id="synthetic-question",
        trace_id="synthetic-trace",
        data=data,
        incomplete=("native_observations_not_loaded",),
    )
    assert data["api_key"] == data["reasoning_content"] == "[redacted]"
    assert "000031" not in json.dumps(data)
    usage = data["budget"]
    assert (
        isinstance(usage, dict) and usage["tokens"] is None and usage["token_upper_bound"] == 2000
    )
    view_names: tuple[DebugView, ...] = ("intent", "turns", "flow")
    views = {view: project(snapshot, view) for view in view_names}
    serialized = json.dumps(views)
    assert all(
        value not in serialized for value in ("000031", "SYNTHETIC_SECRET", "SYNTHETIC_HIDDEN")
    )
    assert {view["run_id"] for view in views.values()} == {snapshot.run_id}
    assert snapshot.data["budget"] is not None
    return {"evidence_kind": "synthetic_debug_snapshot", "remote_model_calls": 0, "views": views}


async def evaluation_demo() -> dict[str, object]:
    cases, audited = audit()  # 包内冻结原件，只审计标签/分组/来源，不执行业务或模型。
    # 报告结构是可扩展字典；实际运行值仍由DTO/门禁逐项检查。
    routes: dict[str, dict[str, Any]] = {
        "course": {
            "rows": [{"score": {"completed": True, "hard_failures": []}}],
            "metrics": {"event_wrong_merge": {"numerator": 0}},
            "checks": {"persistence": True, "replay": True},
        }
    }
    passed = targeted_gate(routes, complete=True)
    interrupted = targeted_gate(routes, complete=False)
    bad = deepcopy(routes)
    bad["course"]["rows"][0]["score"]["hard_failures"] = ["wrong_owner"]
    rejected = targeted_gate(bad, complete=True)
    assert passed["accepted"] and not interrupted["accepted"] and not rejected["accepted"]
    empty = ratio(0, 0, "not_measured")
    assert empty["value"] is None
    return {
        "evidence_kind": "frozen_data_audit_and_synthetic_gate",
        "remote_model_calls": 0,
        "case_count": len(cases),
        "audit": audited,
        "synthetic_selected_gate": passed,
        "interrupted_gate": interrupted,
        "hard_failure_gate": rejected,
        "empty_denominator": empty,
    }

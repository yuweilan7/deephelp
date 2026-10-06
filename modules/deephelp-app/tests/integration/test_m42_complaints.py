"""六叶子新增槽位/许可和旧版本隔离；固定观察不替代真实内容验收。"""

import hashlib
import json
from importlib.resources import files

import pytest
from pydantic import ValidationError

from deephelp_app.adapters.mcp.business.mcp_protocol import canonical, validate_arguments
from deephelp_app.application.sop import SOPExecutor
from deephelp_app.application.sop_governance import RegistryStore, SOPRegistry, schema_signature
from deephelp_app.application.text_entity import TextEntityProcessor
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.intents import IntentCode
from deephelp_app.domain.models.retrieval import CorpusRecord
from deephelp_app.domain.models.tools import (
    ToolName,
    ToolParameters,
    ToolRequest,
    tool_parameters_hash,
)
from deephelp_app.domain.registry import complaint_registry
from deephelp_tools.learning.course.catalog import (
    catalog_question,
    catalog_registry,
    complaints_demo,
)
from deephelp_tools.learning.course.skills import RecordedSkillModel
from deephelp_tools.learning.replay.event_replay import envelope
from deephelp_tools.probes.protocol.mcp_smoke import synthetic_request

pytestmark = pytest.mark.integration


def test_additive_registry_publish_retains_four_leaf_and_rolls_back(tmp_path):
    store = RegistryStore(tmp_path)
    prior = store.publish(catalog_registry())
    latest = store.publish(catalog_registry("complaints-v3"))
    assert store.load(prior).catalog_version == "complaints-v2"
    assert store.load(latest).catalog_version == "complaints-v3"
    assert store.rollback() == prior
    assert store.load(latest).catalog_version == "complaints-v3"


def test_four_leaf_snapshot_and_discovery_remain_exact():
    old = json.loads(
        files("deephelp_app")
        .joinpath("resources/sop_data/business-registry-v3.json")
        .read_text(encoding="utf-8")
    )
    assert (
        catalog_registry().snapshot_hash == "sha256:" + hashlib.sha256(canonical(old)).hexdigest()
    )
    assert (
        schema_signature("complaints-v2")
        == "sha256:54b2e6829c819d0c74b776cdb947f78945a9338d6d525a1528d401d726f731b2"
    )
    assert len(complaint_registry("complaints-v3").actionable_codes) == 6
    for version in ("complaints-v1", "complaints-v2"):
        assert IntentCode.ORDER_CREATION_FAILED not in complaint_registry(version).actionable_codes
        assert (
            IntentCode.REFUND_COUPON_NOT_RETURNED
            not in complaint_registry(version).actionable_codes
        )
        with pytest.raises(ValidationError):
            CorpusRecord(
                doc_id="bad",
                content="下单失败",
                intent_code="ORDER_CREATION_FAILED",
                split="reference",
                source_group="s",
                variant_group="v",
                synthetic=True,
                registry_version=version,
            )


@pytest.mark.parametrize(
    "tool,args",
    [
        ("get_order_creation_failure", {"attempt_id": "TRY-42", "order_id": "ORDER-42"}),
        ("get_order_creation_failure", {"attempt_id": "TRY-42", "user_id": "other"}),
        ("check_refund_coupon_return", {"refund_id": "REF-42"}),
        (
            "check_refund_coupon_return",
            {"refund_id": "REF-42", "coupon_id": "CP-42", "activity_id": "ACT-42"},
        ),
    ],
)
def test_new_tool_rejects_missing_extra_and_owner_arguments(tool, args):
    with pytest.raises(AppError):
        validate_arguments(tool, args)


async def test_request_and_flow_cannot_cross_new_tool_grants():
    req, _, _ = synthetic_request(ExecutionBudget.start(10, 4, 0))
    params = ToolParameters(attempt_id="TRY-42", refund_id="REF-42")
    with pytest.raises(ValidationError):
        ToolRequest.model_validate(
            req.model_copy(
                update={
                    "tool_name": ToolName.GET_ORDER_CREATION_FAILURE,
                    "parameters": params,
                    "parameters_hash": tool_parameters_hash(params),
                }
            ).model_dump()
        )
    reg = catalog_registry("complaints-v3").model_dump(mode="json")
    reg["definitions"][-1]["allowed_tools"] = ["get_order_benefits"]
    with pytest.raises(ValidationError):
        SOPRegistry.model_validate(reg)


async def test_complaint_course_uses_real_sop_states_without_remote_io():
    demo = await complaints_demo()
    assert demo["remote_model_calls"] == demo["database_calls"] == 0
    assert [
        (r["case"], r["status"], r["node_path"][-1] if r["node_path"] else None)
        for r in demo["cases"]
    ] == [
        ("attempt", "RESOLVED", "explained"),
        ("attempt-missing", "WAITING_SLOT", None),
        ("waiting", "RESOLVED", "waiting"),
        ("returned", "RESOLVED", "returned"),
        ("conflict", "HANDED_OFF", "conflict"),
        ("unknown", "HANDED_OFF", "unknown"),
    ]


@pytest.mark.parametrize(
    "raw,code,slots",
    [
        ("下单失败，尝试编号TRY-0042，查原因", "ORDER_CREATION_FAILED", {"attempt_id": "TRY-0042"}),
        (
            "退款编号REF-0042，优惠券CP-0042未退回，查记录",
            "REFUND_COUPON_NOT_RETURNED",
            {"refund_id": "REF-0042", "coupon_id": "CP-0042"},
        ),
    ],
)
async def test_raw_slot_labels_and_rules_match_new_catalog_only(raw, code, slots):
    result = await TextEntityProcessor(catalog_version="complaints-v3").process(
        envelope(raw, "new"), ExecutionBudget.start(10, 4, 0)
    )
    assert {e.name.value: e.value for e in result.entities} == slots
    assert result.rule_matches[0].candidate_code == code
    old = await TextEntityProcessor(catalog_version="complaints-v2").process(
        envelope(raw, "old"), ExecutionBudget.start(10, 4, 0)
    )
    assert all(m.candidate_code != code for m in old.rule_matches)


async def test_missing_coupon_keeps_refund_slot_and_never_dispatches():
    registry = catalog_registry("complaints-v3")
    context = envelope("退款编号REF-42后券没退回", "missing-coupon")
    q, ctx = catalog_question(
        registry,
        code=IntentCode.REFUND_COUPON_NOT_RETURNED,
        slots={"refund_id": "REF-42"},
        context=context,
    )

    class NeverTool:
        async def execute(self, *args, **kwargs):
            raise AssertionError("Missing coupon must stop before dispatch")

    result = await SOPExecutor(
        RecordedSkillModel(), NeverTool(), registry=registry, skills_enabled=False
    ).execute(q, ExecutionBudget.start(10, 4, 0), context=ctx, run_id="missing-coupon")
    assert result.status == "WAITING_SLOT" and result.missing_slots == ("coupon_id",)

"""版本目录实验：固定建议和记录观察复用原SOP，远程模型/数据库均0次。

阅读顺序：catalog_registry → catalog_question → RecordedCatalogFacts → catalog_demo。
固定Port仅提供明确标记的合成观察；真实内容验收须另走Business MCP/PostgreSQL。
这里没有第二套资格查询规则，资格/名额分支仍完全由发布的GovernedSOP执行。
"""

import hashlib
from importlib.resources import files
from typing import Any
from uuid import uuid4

from deephelp_app.adapters.mcp.business.mcp_protocol import canonical
from deephelp_app.application.sop import SOPExecutor
from deephelp_app.application.sop_governance import SOPRegistry
from deephelp_app.domain.checks import validate_tool_context
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import QuestionStatus
from deephelp_app.domain.models.conversation import Question, RequestEnvelope
from deephelp_app.domain.models.entities import Entity, EntityName, EntitySource
from deephelp_app.domain.models.evidence import EvidenceRef, EvidenceSource, Fact, FactKind
from deephelp_app.domain.models.intents import IntentCode
from deephelp_app.domain.models.release import VersionManifest
from deephelp_app.domain.models.tools import ToolRequest, ToolResult, ToolStatus
from deephelp_app.domain.registry import complaint_registry
from deephelp_tools.learning.replay.event_replay import envelope

from .skills import RecordedSkillModel


def catalog_registry() -> SOPRegistry:
    """读取已校验四叶子机器资产；它不会修改任何运行中的发布指针。"""
    return SOPRegistry.model_validate_json(
        files("deephelp_app")
        .joinpath("resources/sop_data/business-registry-v3.json")
        .read_text(encoding="utf-8")
    )


def catalog_question(
    registry: SOPRegistry,
    *,
    activity_id: str = "ACT-42",
    missing: bool = False,
    context: RequestEnvelope | None = None,
) -> tuple[Question, RequestEnvelope]:
    """建立绑定目录/SOP版本的合成问题，实体来源保持当前消息。

    活动主体由可信identity确定，用户没有通过工具参数填写user_id的入口。
    正式Conversation负责持久接收与续接；此工厂只供课程/独立测试固定初态。
    """
    context = context or envelope(
        f"活动编号{activity_id}，我有参与资格和名额吗？", "course-catalog"
    )
    code = IntentCode.ACTIVITY_ELIGIBILITY_QUERY
    question = Question(
        question_id="catalog-" + uuid4().hex,
        identity=context.identity,
        session_id=context.session_id,
        status=QuestionStatus.ACTIVE,
        version=1,
        entities=()
        if missing
        else (
            Entity(
                name=EntityName.ACTIVITY_ID,
                value=activity_id,
                source=EntitySource(message_id=context.message_id, excerpt=activity_id),
            ),
        ),
        member_message_ids=(context.message_id,),
        active_intent=code,
        versions=registry.pin(VersionManifest(), code),
        created_at=context.received_at,
        updated_at=context.received_at,
    )
    return question, context


class RecordedCatalogFacts:
    """明确离线的ToolPort：只回放固定事实，仍经过原请求/参数/证据守卫。"""

    def __init__(self, *, eligibility: str = "eligible", remaining_slots: str = "7") -> None:
        self.eligibility, self.remaining_slots = eligibility, remaining_slots
        self.calls = 0

    async def execute(
        self,
        request: ToolRequest,
        question: Question,
        budget: ExecutionBudget,
        *,
        context: RequestEnvelope,
        on_dispatch: Any = None,
    ) -> ToolResult:
        validate_tool_context(request, question)
        budget.claim_attempt(retry=False)
        budget.tool_steps_used += 1
        self.calls += 1
        call_id = "recorded-call-" + uuid4().hex
        if on_dispatch:
            on_dispatch(call_id)
        assert request.parameters.activity_id is not None
        values = dict(
            activity_id=request.parameters.activity_id,
            activity_label="合成课程活动",
            activity_rules="仅当前主体；资格与名额分别维护。",
            eligibility=self.eligibility,
            remaining_slots=self.remaining_slots,
            eligibility_reason="离线固定观察，非企业记录。",
        )
        evidence_id = "recorded-evidence-" + uuid4().hex
        return ToolResult(
            operation_id=request.operation_id,
            run_id=request.run_id,
            tool_name=request.tool_name,
            tool_version=request.tool_version,
            status=ToolStatus.SUCCEEDED,
            call_id=call_id,
            facts=tuple(
                Fact(name=k, kind=FactKind.TEXT, value=v, evidence_ids=(evidence_id,))
                for k, v in values.items()
            ),
            evidence_refs=(
                EvidenceRef(
                    evidence_id=evidence_id,
                    source=EvidenceSource.TOOL,
                    record_id=call_id,
                    version="business-fixtures-v1",
                    locator=request.parameters.activity_id,
                    content_hash=hashlib.sha256(canonical(values)).hexdigest(),
                    summary="明确离线的固定记录观察。",
                ),
            ),
        )


async def catalog_demo() -> dict[str, object]:
    """正常、未知名额与缺槽三例；同预算有界执行，缺槽不进入任何Port。"""
    registry = catalog_registry()
    cases = []
    for name, state, remaining, missing in [
        ("eligible", "eligible", "7", False),
        ("unknown", "unknown", "unknown", False),
        ("missing", "eligible", "7", True),
    ]:
        question, context = catalog_question(registry, missing=missing)
        budget = ExecutionBudget.start(10, 4, 0)
        port = RecordedCatalogFacts(eligibility=state, remaining_slots=remaining)
        result = await SOPExecutor(
            RecordedSkillModel(), port, registry=registry, skills_enabled=False
        ).execute(question, budget, context=context, run_id="course-catalog-" + name)
        cases.append(
            dict(
                case=name,
                status=result.status.value,
                node_path=result.node_path,
                tool_calls=port.calls,
                result=result.model_dump(mode="json"),
            )
        )
    return dict(
        remote_model_calls=0,
        database_calls=0,
        catalog=complaint_registry("complaints-v2").model_dump(mode="json"),
        cases=cases,
    )

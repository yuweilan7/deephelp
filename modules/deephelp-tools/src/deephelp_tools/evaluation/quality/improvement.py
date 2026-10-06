"""开发差例驱动的一轮审核/修订/锁定holdout判断，共用原Prompt资产与发布入口。

review先冻结问题、改动和采用标准，revise才生成候选；close只按预定标准评分。
AI开发审查明确披露，不冒充人类业务审核。拒绝是有效闭环，ADOPTION_ELIGIBLE也仍需原完整发布门禁。
"""

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from deephelp_app.adapters.releases.asset_integrity import file_digest
from deephelp_app.application.corpus import digest
from deephelp_app.application.intent_prompt import (
    PromptArtifact,
    default_system,
    fallback_contract,
    read_prompt,
)
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.models.common import DTO
from deephelp_tools.evaluation.quality.paired import Experiment, report


class Criteria(DTO):
    """修订前的最低收益、完整分母和关键反例，不能看到holdout后改数字。"""

    minimum_accuracy_gain: float = Field(default=0.05, ge=0.01, le=1)
    maximum_regressions: Literal[0] = 0
    minimum_cases: int = Field(default=16, ge=8, le=1000)
    minimum_critical_support: int = Field(default=3, ge=1, le=100)
    maximum_sign_p: float = Field(default=0.05, ge=0.001, le=0.1)


class Proposal(DTO):
    issue_turn_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    problem: str = Field(min_length=10, max_length=2000)
    addition: str = Field(min_length=10, max_length=4000)
    reviewer: str = Field(min_length=1, max_length=128)
    reviewer_kind: Literal["ai_developer_review", "human_developer_review"]
    rationale: str = Field(min_length=10, max_length=2000)
    criteria: Criteria = Field(default_factory=Criteria)


def quality_checks(criteria: Criteria, observed: dict[str, Any]) -> dict[str, bool]:
    """纯采用标准，真实闭环与离线课程共用，缺少任何必要证据都不能放行。"""
    m = observed["routes"]["candidate"]["metrics"]
    critical = m["per_label"]
    return dict(
        complete=observed["status"] == "COMPLETE",
        sufficient_cases=m["denominator"] >= criteria.minimum_cases,
        minimum_gain=observed["paired"]["accuracy_delta"] >= criteria.minimum_accuracy_gain,
        paired_evidence=observed["paired"]["exact_sign_p_two_sided"] <= criteria.maximum_sign_p,
        no_regression=len(observed["paired"]["regressed"]) <= criteria.maximum_regressions,
        no_protocol_error=m["errors"] == 0,
        critical_support=all(
            critical[k]["support"] >= criteria.minimum_critical_support
            for k in ("unknown", "multiple")
        ),
        critical_correct=all(
            critical[k]["true_positive"] == critical[k]["support"] for k in ("unknown", "multiple")
        ),
    )


def review(dev_path: Path, proposal: Proposal, owner: dict[str, str]) -> dict[str, Any]:
    """只消费完整dev差例，不读holdout；审核依据、主体声明与文件字节共同固定。"""
    dev = json.loads(dev_path.read_text(encoding="utf-8"))
    spec = Experiment.model_validate(dev["contract"])
    rows = dev["rows"]
    scored = report(spec, rows, [r["turn_id"] for r in rows])
    if dev["status"] != "COMPLETE" or spec.split != "dev" or dev["paired"] != scored["paired"]:
        raise ConfigurationError("Review needs complete reproducible development evidence")
    failures = {r["turn_id"] for r in rows if r["baseline"]["label"] != r["gold"]}
    if not set(proposal.issue_turn_ids) <= failures or len(set(proposal.issue_turn_ids)) != len(
        proposal.issue_turn_ids
    ):
        raise ConfigurationError("Proposed issue is not a distinct baseline development failure")
    value = dict(
        format="m45-improvement-review-v1",
        owner=owner,
        dev_fingerprint=spec.fingerprint,
        dataset_id=spec.dataset_id,
        freeze_hash=spec.freeze_hash,
        source_kind=dev["source_kind"],
        dev_path=str(dev_path.resolve()),
        dev_hash=file_digest(dev_path),
        baseline_prompt=spec.baseline.system_prompt,
        proposal=proposal.model_dump(mode="json"),
        scope=spec.target,
        state="APPROVED_FOR_EXPERIMENT",
        note=(
            "Developer review only. AI review is disclosed; "
            "original human business/release guards remain."
        ),
    )
    value["review_hash"] = digest(value)
    return value


def verify_review(value: dict[str, Any]) -> None:
    if (
        value.get("format") != "m45-improvement-review-v1"
        or value.get("review_hash")
        != digest({k: v for k, v in value.items() if k != "review_hash"})
        or file_digest(Path(value["dev_path"])) != value["dev_hash"]
    ):
        raise ConfigurationError("Reviewed problem / criteria / original dev bytes changed")
    Proposal.model_validate(value["proposal"])


def revise(value: dict[str, Any]) -> PromptArtifact:
    """已固定criteria之后才生成唯一候选；不修改包内default或原正式Prompt。"""
    verify_review(value)
    proposal = Proposal.model_validate(value["proposal"])
    if value["baseline_prompt"] != default_system("complaints-v3"):
        raise ConfigurationError("This one-round revision requires the pinned original baseline")
    text = value["baseline_prompt"] + "\n\n" + proposal.addition
    contract = fallback_contract("complaints-v3")
    return PromptArtifact(
        name="m45-reviewed-" + value["review_hash"][:20],
        version=1,
        text=text,
        contract=contract,
        request_digest=digest([text, contract]),
    )


def close(
    value: dict[str, Any], prompt_path: Path, holdout_path: Path, contract_path: Path
) -> dict[str, Any]:
    """一次锁定holdout的采用/拒绝，不能把PARTIAL、样本不足或关键退化算成收益。"""
    verify_review(value)
    candidate = read_prompt(prompt_path, "complaints-v3")
    if candidate != revise(value):
        raise ConfigurationError("Candidate differs from pre-reviewed single revision")
    observed = json.loads(holdout_path.read_text(encoding="utf-8"))
    spec = Experiment.model_validate(observed["contract"])
    registered = json.loads(contract_path.read_text(encoding="utf-8"))
    if (
        spec.split != "holdout"
        or spec.review_hash != value["review_hash"]
        or spec.dataset_id != value["dataset_id"]
        or spec.freeze_hash != value["freeze_hash"]
        or spec.factor != "fallback_prompt"
        or spec.baseline.route != "hybrid"
        or spec.candidate.route != "hybrid"
        or spec.baseline.system_prompt != value["baseline_prompt"]
        or spec.candidate.system_prompt != candidate.text
        or spec.fingerprint != observed["experiment_fingerprint"]
        or registered["experiment_fingerprint"] != spec.fingerprint
        or registered["contract"] != spec.model_dump(mode="json")
    ):
        raise ConfigurationError("Holdout is not this reviewed single-factor contract")
    spec.require_factor()
    from deephelp_tools.evaluation.quality.paired_cli import snapshot
    from deephelp_tools.evaluation.quality.paired_runtime import validate_contract

    repair_path = holdout_path.parent / "repair.json"
    expected = validate_contract(
        spec, snapshot(Path(registered["paths"]["dataset"])), repair_path=repair_path
    )
    projected = [
        {k: r[k] for k in ("case_id", "turn_id", "text", "gold")} for r in observed["rows"]
    ]
    if projected != expected:
        raise ConfigurationError("Holdout rows differ from frozen full input/gold denominator")
    recomputed = report(spec, observed["rows"], [r["turn_id"] for r in observed["rows"]])
    if observed["routes"] != recomputed["routes"] or observed["paired"] != recomputed["paired"]:
        raise ConfigurationError("Metrics changed from original paired observations")
    criteria = Proposal.model_validate(value["proposal"]).criteria
    checks = quality_checks(criteria, observed)
    files = {
        str(p.resolve()): file_digest(p)
        for p in (Path(value["dev_path"]), prompt_path, holdout_path, contract_path)
    }
    if repair_path.is_file():
        files[str(repair_path.resolve())] = file_digest(repair_path)
    result = dict(
        format="m45-improvement-closure-v1",
        review=value,
        review_hash=value["review_hash"],
        candidate_request_digest=candidate.request_digest,
        holdout_fingerprint=spec.fingerprint,
        files=files,
        checks=checks,
        state="ADOPTION_ELIGIBLE" if all(checks.values()) else "REJECTED",
        paths=dict(
            prompt=str(prompt_path.resolve()),
            holdout=str(holdout_path.resolve()),
            contract=str(contract_path.resolve()),
        ),
        failed_criteria=[k for k, v in checks.items() if not v],
        metrics=observed["routes"],
        paired=observed["paired"],
        active_changed=False,
        note=(
            "Eligibility still requires original complete content/release/install/recovery "
            "guards; rejection retains baseline."
        ),
    )
    result["closure_hash"] = digest(result)
    return result


def require_eligible(path: Path, prompt_path: Path | None) -> dict[str, str]:
    """供原prepare_manifest的附加门禁；旧门禁不会被本评测替代或自动批准。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        value.get("format") != "m45-improvement-closure-v1"
        or value.get("closure_hash")
        != digest({k: v for k, v in value.items() if k != "closure_hash"})
        or value["state"] != "ADOPTION_ELIGIBLE"
        or not all(value["checks"].values())
        or prompt_path is None
    ):
        raise ConfigurationError("Independent reviewed improvement is not eligible for release")
    if any(file_digest(Path(p)) != sha for p, sha in value["files"].items()):
        raise ConfigurationError("Reviewed quality evidence bytes changed")
    if (
        read_prompt(prompt_path, "complaints-v3").request_digest
        != value["candidate_request_digest"]
    ):
        raise ConfigurationError("Release Prompt differs from reviewed improvement")
    rebuilt = close(
        value["review"],
        Path(value["paths"]["prompt"]),
        Path(value["paths"]["holdout"]),
        Path(value["paths"]["contract"]),
    )
    if rebuilt != value:
        raise ConfigurationError("Quality closure differs from verified original criteria/results")
    result: dict[str, str] = value["files"] | {str(path.resolve()): file_digest(path)}
    return result

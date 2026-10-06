"""发布质量与来源反例独立断言；离线不把模型预测当审核或实际安装证据。"""

import copy

import pytest

from deephelp_app.adapters.releases.corpus_binding import require_content, require_quality
from deephelp_app.application.corpus import digest
from deephelp_app.application.corpus_authoring import (
    delete_record,
    edit_record,
    empty_workspace,
    feedback_record,
    freeze_workspace,
    import_feedback,
    review_records,
    version_diff,
)
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.feedback import FeedbackCandidate, RouteReview
from deephelp_app.domain.registry import complaint_registry
from deephelp_tools.evaluation.retrieval.corpus_release import metric_changes


def candidate(**changes):
    return FeedbackCandidate(
        candidate_id="a" * 64,
        run_id="original-run",
        source_group="source",
        variant_group="source",
        text="订单是否挂了本轮促销活动关联，请核对",
        observation={},
        **changes,
    )


def approval(**changes):
    return RouteReview.model_validate(
        dict(
            state="approved",
            label="ORDER_ACTIVITY_QUERY",
            reviewer="owner/user",
            method="human",
            reason="人工核对来源与标签",
            independent_from_heldout=True,
        )
        | changes
    )


@pytest.mark.parametrize(
    "changes",
    [
        dict(state="withdrawn"),
        dict(state="disputed"),
        dict(method="deterministic_evidence"),
        dict(independent_from_heldout=False),
        dict(label=None),
    ],
)
def test_prediction_or_stale_review_cannot_be_gold(changes):
    with pytest.raises(AppError):
        feedback_record(candidate(reviews={"corpus": approval(**changes)}), "complaints-v3")


def test_feedback_draft_revision_and_old_version_survive_edit_and_deletion():
    owner = dict(tenant_id="owner", user_id="user")
    source = candidate(reviews={"corpus": approval()}, revision=1)
    row = import_feedback(empty_workspace("complaints-v3"), source)
    doc = next(iter(row["entries"]))
    assert row["entries"][doc]["status"] == "DRAFT"
    with pytest.raises(AppError):
        freeze_workspace(row, "v1", owner)
    reviewed = review_records(row, (doc,), True, "检查原run", owner)
    old = freeze_workspace(reviewed, "v1", owner)
    changed = edit_record(
        reviewed, feedback_record(source, "complaints-v3"), "修订来源", create=False
    )
    assert "feedback" not in changed["entries"][doc]
    assert changed["entries"][doc]["revision"] == 2
    with pytest.raises(AppError):
        freeze_workspace(changed, "v2", owner)
    new = freeze_workspace(review_records(changed, (doc,), True, "复核修订", owner), "v2", owner)
    assert list(version_diff(old, new)["changed"]) == [doc]
    assert old["entries"][doc]["feedback"]["run_id"] == "original-run"
    assert not delete_record(changed, doc)["entries"] and old["entries"]
    foreign = copy.deepcopy(new)
    foreign["owner"] = dict(tenant_id="other", user_id="user")
    # 即便外主体自行重新冻结，也不允许拿来比较原主体。
    foreign = freeze_workspace(
        review_records(changed, (doc,), True, "外审核", foreign["owner"]), "v3", foreign["owner"]
    )
    with pytest.raises(AppError):
        version_diff(old, foreign)


def evidence():
    golds = [c.value for c in complaint_registry("complaints-v3").actionable_codes] + [None, None]
    retrieval = dict(
        observations=[dict(query_id=str(i), gold=g, split="test") for i, g in enumerate(golds)],
        metrics={
            s: {
                v: {
                    m: dict(recall_1=0.8, recall_3=1.0, candidate_recall_1=0.8, mrr=0.9)
                    for m in ("dense", "bm25", "hybrid")
                }
                for v in ("published", "candidate")
            }
            for s in ("dev", "test")
        },
    )
    gate = dict(
        rows=[
            dict(query_id=str(i), gold=g, final_code=g, classification_correct=True, version=v)
            for v in ("published", "candidate")
            for i, g in enumerate(golds)
        ]
    )
    return gate, retrieval


def test_quality_recomputed_instead_of_trusting_pass_flag():
    gate, retrieval = evidence()
    require_quality(gate, retrieval, "complaints-v3")
    assert len(metric_changes(retrieval)) == 24
    assert all(r["result"] == "unchanged" for r in metric_changes(retrieval))
    retrieval["metrics"]["test"]["candidate"]["hybrid"]["mrr"] = 0.85
    assert metric_changes(retrieval)[-1]["result"] == "regressed"
    with pytest.raises(ConfigurationError, match="regressed"):
        require_quality(gate, retrieval, "complaints-v3")


@pytest.mark.parametrize("failure", ["missing", "unknown", "gold", "duplicate", "leaf"])
def test_classification_coverage_and_unknown_are_required(failure):
    gate, retrieval = evidence()
    if failure == "missing":
        gate["rows"].pop()
    if failure == "duplicate":
        gate["rows"].append(gate["rows"][-1])
    if failure == "unknown":
        next(r for r in gate["rows"] if r["version"] == "candidate" and r["gold"] is None)[
            "final_code"
        ] = "COUPON_UNUSABLE"
    if failure == "gold":
        next(r for r in gate["rows"] if r["version"] == "candidate" and r["gold"] is not None)[
            "gold"
        ] = None
    if failure == "leaf":
        retrieval["observations"].pop(0)
    with pytest.raises(ConfigurationError):
        require_quality(gate, retrieval, "complaints-v3")


def test_retrieval_or_feedback_success_cannot_replace_full_business_content():
    binding = dict(version_hash="a" * 64)
    ids = [
        "order-adjacent",
        "coupon-adjacent",
        "discount-adjacent",
        "eligible",
        "attempt",
        "refund-pending",
        "unsupported-logistics",
        "unsupported-refund",
    ]
    valid = dict(
        corpus_binding_digest=digest(binding),
        status="PASS",
        phase="release",
        rows=[dict(case_id=k) for k in ids],
        checks={"pg_snapshot": True, "replay": True},
    )
    require_content(valid, binding)
    for change in (
        dict(phase="corpus-feedback"),
        dict(rows=valid["rows"][:3]),
        dict(checks={"pg_snapshot": False}),
        dict(corpus_binding_digest="b" * 64),
    ):
        with pytest.raises(ConfigurationError):
            require_content(valid | change, binding)

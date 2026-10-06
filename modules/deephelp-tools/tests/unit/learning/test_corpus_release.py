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


def test_same_session_reuse_rejects_even_one_changed_execution_byte(tmp_path, monkeypatch):
    import json

    import deephelp_tools.probes.conversation.memory_content_review as review

    old = {"adapters/releases/memory_binding.py": "old-guard", "application/sop.py": "same"}
    before = tmp_path / "before.json"
    before.write_text(json.dumps({"package_digest": digest(old), "artifact_hashes": old}))
    now = old | {"adapters/releases/memory_binding.py": "new-guard"}
    monkeypatch.setattr(
        review,
        "provenance",
        lambda: {
            "package_digest": digest(now),
            "artifact_hashes": now,
        },
    )
    assert review.verify_runtime_delta(before)["unchanged_files"] == 1
    now["application/sop.py"] = "changed execution"
    with pytest.raises(ConfigurationError, match="unchanged model/business/DTO/UI"):
        review.verify_runtime_delta(before)


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
        contract=dict(deployed_route="hybrid"),
        rows=[
            dict(
                query_id=str(i),
                gold=g,
                final_code=g,
                classification_correct=True,
                version=v,
                route="hybrid",
            )
            for v in ("published", "candidate")
            for i, g in enumerate(golds)
        ],
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


async def test_known_retrieval_regression_rejects_before_chat(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    import deephelp_tools.evaluation.retrieval.corpus_release as module

    _, retrieval = evidence()
    retrieval.update(status="PASS", observations=[])
    retrieval["metrics"]["test"]["candidate"]["hybrid"]["mrr"] = 0.85
    (tmp_path / "retrieval-gate.json").write_text(json.dumps(retrieval))
    policy = tmp_path / "policy.json"
    policy.write_text("{}")

    class NeverChat:
        async def chat(self, *args, **kwargs):
            raise AssertionError("Known regression cannot dispatch a paid classification")

    monkeypatch.setattr(module, "freeze_files", lambda *args: tmp_path)
    monkeypatch.setattr(
        module, "verify_files", lambda *args, **kwargs: {"providers_digest": "provider"}
    )
    monkeypatch.setattr(module, "catalog_binding", lambda *args: {"cascade_policy": str(policy)})
    monkeypatch.setattr(module, "dataset", lambda *args: (None, {"dev": [], "test": []}))
    result = await module.quality_gate(
        tmp_path,
        {"version_hash": "a" * 64, "catalog_version": "complaints-v3"},
        SimpleNamespace(gateway=NeverChat(), retrieval_binding=SimpleNamespace(mode="hybrid")),
        lambda: None,
        policy,
    )
    assert result["status"] == "FAIL" and result["rows"] == []
    assert result["checks"] == {"retrieval_no_regression": False}
    assert result["classification_accuracy"] == dict(candidate=None, published=None)


def test_diagnostic_decline_remains_visible_and_route_cannot_be_selected_by_browser():
    gate, retrieval = evidence()
    retrieval["metrics"]["test"]["candidate"]["bm25"]["mrr"] = 0.85
    require_quality(gate, retrieval, "complaints-v3")
    assert any(
        r["route"] == "bm25" and r["result"] == "regressed" for r in metric_changes(retrieval)
    )
    with pytest.raises(ConfigurationError, match="deployed"):
        require_quality(gate, retrieval, "complaints-v3", "bm25")
    gate["rows"][0]["route"] = "dense"
    with pytest.raises(ConfigurationError):
        require_quality(gate, retrieval, "complaints-v3")


def test_content_repair_reuses_only_completed_exact_target_rows(tmp_path):
    import json

    from deephelp_tools.probes.conversation.catalog_probe import merge_content, reusable_cases

    signature = {
        k: "a" * 64
        for k in (
            "code_digest",
            "providers_digest",
            "pointer_digest",
            "assets_digest",
            "corpus_binding_digest",
            "sop_review_digest",
            "skills_review_digest",
        )
    }
    old = signature | dict(
        phase="release",
        status="FAIL",
        rows=[dict(case_id="leaf"), dict(case_id="unknown")],
        checks={
            "leaf:intent": True,
            "leaf:history": True,
            "leaf:zero_call_replay": True,
            "unknown:no_tool": True,
            "unknown:candidate_snapshot": False,
        },
    )
    new = signature | dict(
        phase="release",
        status="PASS",
        rows=[dict(case_id="unknown")],
        checks={
            "unknown:no_tool": True,
            "unknown:candidate_snapshot": True,
            "unknown:history": True,
            "unknown:zero_call_replay": True,
        },
    )
    required = ["leaf", "unknown"]
    assert reusable_cases(old, signature, required) == ["leaf"]
    assert reusable_cases(old, signature | dict(code_digest="b" * 64), required) == []
    incomplete = copy.deepcopy(old)
    incomplete["checks"].pop("leaf:history")
    assert reusable_cases(incomplete, signature, required) == []
    original, partial = tmp_path / "failed.json", tmp_path / "partial.json"
    original.write_text(json.dumps(old))
    partial.write_text(json.dumps(new))
    before = original.read_bytes()
    merged = merge_content(original, partial, ["leaf"], required)
    assert merged["status"] == "PASS" and original.read_bytes() == before
    assert merged["observed_source"]["sha256"] and len(merged["rows"]) == 2
    new["code_digest"] = "changed"
    partial.write_text(json.dumps(new))
    with pytest.raises(ConfigurationError):
        merge_content(original, partial, ["leaf"], required)


def test_full_content_verification_binds_original_and_does_not_claim_new_calls(tmp_path):
    import json

    from deephelp_tools.probes.conversation.catalog_probe import reuse_full_content

    expected = {"code_digest": "runtime", "providers_digest": "same"}
    report = expected | dict(
        phase="release",
        status="PASS",
        tooling_digest="original",
        rows=[dict(case_id="one")],
        checks={"one:history": True, "one:zero_call_replay": True},
        api_observations=[dict(model="original")],
        observed_source={"path": "failed.json", "sha256": "old"},
        content_sources={"part.json": "partial"},
    )
    path = tmp_path / "full.json"
    path.write_text(json.dumps(report))
    reviewed = reuse_full_content(path, expected, ["one"], "reviewer")
    assert reviewed["tooling_digest"] == "reviewer" and reviewed["api_observations"] == []
    assert reviewed["observed_source"]["path"] == str(path.resolve())
    assert reviewed["content_sources"] == {"part.json": "partial", "failed.json": "old"}
    with pytest.raises(ConfigurationError):
        reuse_full_content(path, {"code_digest": "changed"}, ["one"], "reviewer")

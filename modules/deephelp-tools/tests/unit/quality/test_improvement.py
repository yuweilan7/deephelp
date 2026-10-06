"""事前审核、留出分母、负面结果与原发布拒绝的独立断言；固定观察不证明模型质量。"""

import copy
import json

import pytest

from deephelp_app.application.intent_prompt import default_system
from deephelp_app.domain.errors import ConfigurationError
from deephelp_tools.evaluation.quality.improvement import (
    Proposal,
    close,
    require_eligible,
    review,
    revise,
)
from deephelp_tools.evaluation.quality.paired import Experiment, Variant, report
from deephelp_tools.evaluation.releases.release_prepare import prepare_manifest


def fixture(tmp_path, monkeypatch, *, gain=True):
    import deephelp_tools.evaluation.quality.paired_runtime as runtime

    system = default_system("complaints-v3")
    devspec = Experiment(
        dataset_id="data",
        freeze_hash="freeze",
        split="dev",
        factor="retrieval_route",
        baseline=Variant(route="hybrid", system_prompt=system),
        candidate=Variant(route="dense", system_prompt=system),
        selection_hash="dev",
        files={},
        source={},
    )
    devrows = [
        dict(
            turn_id="dev-1",
            gold="unknown",
            baseline=dict(label="DISCOUNT_MISSING"),
            candidate=dict(label="DISCOUNT_MISSING"),
        )
    ]
    dev = tmp_path / "dev.json"
    dev.write_text(json.dumps(report(devspec, devrows, ["dev-1"])))
    proposal = Proposal(
        issue_turn_ids=("dev-1",),
        problem="金额方向不明确被过度判supported",
        addition="对于金额方向不明确的独立输入应拒识，不依据例子补事实。",
        reviewer="codex-primary",
        reviewer_kind="ai_developer_review",
        rationale="只根据开发差例审查修订，未知样本不用于调参。",
    )
    approved = review(dev, proposal, dict(tenant_id="owner", user_id="user"))
    prompt = revise(approved)
    path = tmp_path / "prompt.json"
    path.write_text(prompt.model_dump_json())
    spec = devspec.model_copy(
        update=dict(
            split="holdout",
            factor="fallback_prompt",
            review_hash=approved["review_hash"],
            candidate=Variant(route="hybrid", system_prompt=prompt.text),
        )
    )
    labels = ["unknown"] * 3 + ["multiple"] * 3 + ["DISCOUNT_MISSING"] * 10
    rows = [
        dict(
            case_id=str(i),
            turn_id=str(i),
            text="synthetic " + str(i),
            gold=g,
            baseline=dict(label="DISCOUNT_MISSING" if (i < 6 if gain else i == 0) else g),
            candidate=dict(label=g if gain or i != 0 else "DISCOUNT_MISSING"),
        )
        for i, g in enumerate(labels)
    ]
    monkeypatch.setattr(
        runtime,
        "validate_contract",
        lambda *_, **__: [{k: r[k] for k in ("case_id", "turn_id", "text", "gold")} for r in rows],
    )
    frozen = tmp_path / "dataset.json"
    frozen.write_text("{}")
    contract = tmp_path / "contract.json"
    contract.write_text(
        json.dumps(
            dict(
                experiment_fingerprint=spec.fingerprint,
                contract=spec.model_dump(mode="json"),
                paths=dict(dataset=str(frozen)),
            )
        )
    )
    holdout = tmp_path / "holdout.json"
    holdout.write_text(json.dumps(report(spec, rows, [r["turn_id"] for r in rows])))
    return approved, path, holdout, contract, dev


def test_pre_review_criteria_immutable_and_issue_must_be_dev_failure(tmp_path, monkeypatch):
    value, *_ = fixture(tmp_path, monkeypatch)
    changed = copy.deepcopy(value)
    changed["proposal"]["criteria"]["minimum_accuracy_gain"] = 0.01
    with pytest.raises(ConfigurationError, match="changed"):
        revise(changed)
    assert value["proposal"]["reviewer_kind"] == "ai_developer_review"
    bad = Proposal.model_validate(value["proposal"]).model_copy(
        update={"issue_turn_ids": ("not-dev",)}
    )
    with pytest.raises(ConfigurationError, match="failure"):
        review(tmp_path / "dev.json", bad, value["owner"])


@pytest.mark.parametrize("gain", [False, True])
def test_predetermined_adopt_or_reject_and_complete_release_gate(tmp_path, monkeypatch, gain):
    value, prompt, held, contract, _ = fixture(tmp_path, monkeypatch, gain=gain)
    result = close(value, prompt, held, contract)
    assert result["state"] == ("ADOPTION_ELIGIBLE" if gain else "REJECTED")
    assert result["active_changed"] is False
    closed = tmp_path / "closed.json"
    closed.write_text(json.dumps(result))
    if gain:
        assert str(closed.resolve()) in require_eligible(closed, prompt)
        held.write_text(held.read_text() + " ")
        with pytest.raises(ConfigurationError, match="bytes"):
            require_eligible(closed, prompt)
    else:
        assert (
            "minimum_gain" in result["failed_criteria"]
            and "critical_correct" in result["failed_criteria"]
        )
        with pytest.raises(ConfigurationError, match="eligible"):
            prepare_manifest(
                tmp_path / "missing-assets",
                tmp_path / "missing-providers",
                None,
                "unused",
                quality_review=closed,
                prompt_artifact=prompt,
            )


def test_holdout_denominator_prompt_and_metric_tamper_rejected(tmp_path, monkeypatch):
    value, prompt, held, contract, _ = fixture(tmp_path, monkeypatch)
    data = json.loads(held.read_text())
    data["rows"] = data["rows"][:-1]
    held.write_text(json.dumps(data))
    with pytest.raises(ConfigurationError, match="denominator"):
        close(value, prompt, held, contract)


def test_review_refuses_holdout_as_development(tmp_path, monkeypatch):
    value, prompt, held, contract, _ = fixture(tmp_path, monkeypatch)
    with pytest.raises(ConfigurationError, match="development"):
        review(held, Proposal.model_validate(value["proposal"]), value["owner"])

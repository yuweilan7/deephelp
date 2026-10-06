"""完整分母/小样本/合同漂移与恢复反例；固定观察验证统计而非模拟真实质量。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deephelp_app.adapters.releases.asset_integrity import file_digest
from deephelp_app.domain.errors import ConfigurationError
from deephelp_tools.evaluation.quality.paired import Experiment, Variant, label, report
from deephelp_tools.evaluation.quality.paired_runtime import execute, validate_contract


def spec(**updates):
    return Experiment(
        dataset_id="data",
        freeze_hash="frozen",
        split="dev",
        factor="retrieval_route",
        baseline=Variant(route="hybrid", system_prompt="same"),
        candidate=Variant(route="dense", system_prompt="same"),
        selection_hash="selection",
        files={},
        source={},
        **updates,
    )


def test_full_denominators_errors_and_paired_discordance():
    rows = [
        dict(
            turn_id="a",
            gold="unknown",
            baseline=dict(label="unknown"),
            candidate=dict(label="multiple"),
        ),
        dict(
            turn_id="b",
            gold="multiple",
            baseline=dict(error="timeout"),
            candidate=dict(label="multiple"),
        ),
        dict(
            turn_id="c",
            gold="DISCOUNT_MISSING",
            baseline=dict(label="DISCOUNT_MISSING", ranking=dict(recall_1=1, mrr=1)),
            candidate=dict(label="DISCOUNT_MISSING", ranking=dict(recall_1=0, mrr=0)),
        ),
    ]
    result = report(spec(), rows, ["a", "b", "c"])
    assert result["status"] == "PARTIAL"
    a = result["routes"]["baseline"]["metrics"]
    assert (a["denominator"], a["correct"], a["errors"]) == (3, 2, 1)
    assert a["confusion"]["multiple"] == {"__ERROR__": 1}
    assert a["supported_denominator"] == 1 and a["retrieval"]["recall_1"] == 1
    assert a["reject_denominator"] == 2 and a["reject_correct"] == 1
    assert result["paired"]["improved"] == ["b"] and result["paired"]["regressed"] == ["a"]
    assert (
        result["paired"]["accuracy_delta"] == 0 and result["paired"]["exact_sign_p_two_sided"] == 1
    )
    assert a["accuracy_wilson95"][0] < 0.3 and a["accuracy_wilson95"][1] > 0.9
    with pytest.raises(ConfigurationError):
        report(spec(), rows, ["a", "b"])


def test_single_factor_and_label_guards():
    spec().require_factor()
    with pytest.raises(ConfigurationError):
        spec().model_copy(
            update={"candidate": Variant(route="dense", system_prompt="changed")}
        ).require_factor()
    with pytest.raises(ConfigurationError):
        spec().model_copy(update={"split": "holdout"}).require_factor()
    assert label(dict(final_code=None, reason="fallback_multiple")) == "multiple"
    assert label(dict(final_code=None, reason="fallback_unknown")) == "unknown"
    with pytest.raises(ConfigurationError):
        label(dict(final_code="unregistered"))


@pytest.mark.asyncio
async def test_completed_resume_zero_calls_and_changed_source_rejected(tmp_path: Path, monkeypatch):
    # 不需连接Fake模型：已完成journal必须在create_client之前直接返回。
    import deephelp_tools.evaluation.quality.paired_runtime as runtime

    frozen = {"dataset_id": "data", "freeze_hash": "frozen"}
    monkeypatch.setattr(runtime, "validate_contract", lambda *_: [])
    monkeypatch.setattr(
        runtime, "create_client", lambda *_: pytest.fail("completed replay opened client")
    )
    contract = tmp_path / "contract.json"
    contract.write_text(
        json.dumps(
            dict(
                contract=spec().model_dump(mode="json"),
                experiment_fingerprint=spec().fingerprint,
                paths={},
            )
        )
    )
    directory = tmp_path / "run"
    directory.mkdir()
    (directory / "report.json").write_text(json.dumps(dict(status="COMPLETE")))
    (directory / "journal.json").write_text(
        json.dumps(
            dict(
                experiment_fingerprint=spec().fingerprint,
                complete=True,
                executions=1,
                api_records=[],
                remote_attempts=0,
                report_hash=file_digest(directory / "report.json"),
            )
        )
    )
    (directory / "report.json").write_text(json.dumps(dict(status="COMPLETE")))
    gateway = SimpleNamespace(diagnostics=[])
    gate = SimpleNamespace(state={"attempts": 10})
    assert (await execute(contract, frozen, directory, gateway, gateway, gate))[
        "status"
    ] == "COMPLETE"
    assert not (directory / ".writer").exists()
    monkeypatch.setattr(runtime, "validate_contract", validate_contract)
    monkeypatch.setattr(runtime, "selected", lambda *_: [])
    with pytest.raises(ConfigurationError):
        validate_contract(spec(), frozen)


@pytest.mark.asyncio
async def test_unknown_requires_explicit_resume_and_releases_lock(tmp_path, monkeypatch):
    import deephelp_tools.evaluation.quality.paired_runtime as runtime

    monkeypatch.setattr(runtime, "validate_contract", lambda *_: [])
    contract = tmp_path / "contract.json"
    contract.write_text(
        json.dumps(
            dict(
                contract=spec().model_dump(mode="json"),
                experiment_fingerprint=spec().fingerprint,
                paths={},
            )
        )
    )
    directory = tmp_path / "run"
    directory.mkdir()
    (directory / "journal.json").write_text(
        json.dumps(
            dict(
                experiment_fingerprint=spec().fingerprint,
                pending={"phase": "candidate"},
                api_records=[],
                remote_attempts=1,
            )
        )
    )
    gateway = SimpleNamespace(diagnostics=[])
    with pytest.raises(ConfigurationError, match="UNKNOWN"):
        await execute(
            contract, {}, directory, gateway, gateway, SimpleNamespace(state={"attempts": 1})
        )
    assert not (directory / ".writer").exists()

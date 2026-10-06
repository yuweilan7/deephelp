"""发布前拒绝混配：独立构造资产，不能仅靠PASS文字或旧三标签请求放行。"""

import json
from pathlib import Path

import pytest

from deephelp_app.adapters.releases.catalog_binding import catalog_binding
from deephelp_app.application.corpus import read_corpus
from deephelp_app.application.intent import INTENT_SCHEMA
from deephelp_app.application.intent_prompt import fallback_contract
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.models.retrieval import HybridScope
from deephelp_tools.assets import asset_path

pytestmark = pytest.mark.unit


def write(path: Path, data: object) -> str:
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_prompt_preserves_old_contract_and_explicit_six_leaf_schema():
    assert fallback_contract()["output_schema"] == INTENT_SCHEMA
    codes = fallback_contract("complaints-v3")["output_schema"]["properties"]["code"]["enum"]
    assert set(codes) == {
        None,
        "DISCOUNT_MISSING",
        "COUPON_UNUSABLE",
        "ORDER_ACTIVITY_QUERY",
        "ACTIVITY_ELIGIBILITY_QUERY",
        "ORDER_CREATION_FAILED",
        "REFUND_COUPON_NOT_RETURNED",
    }


@pytest.fixture
def assets(tmp_path):
    corpus = asset_path("m42_catalog_v3_corpus.jsonl")
    scope = HybridScope(
        index_kind="intent_hybrid",
        registry_version="complaints-v3",
        namespace="m42_test",
        dataset_version="m42-test",
        analyzer_version="jieba-search-cn-lower-v1",
        signature={
            "provider": "test",
            "model": "test",
            "dimension": 1024,
            "normalization": "l2",
            "text_normalization": "nfc-strip-v1",
        },
    )
    corpus_digest = read_corpus(corpus).index_digest
    policy = write(
        tmp_path / "policy.json",
        {
            "format": "m12-cascade-v1",
            "dev_digest": "dev",
            "gates": {},
            "scope_fingerprint": scope.fingerprint,
            "dense_weight": 0.5,
            "corpus_digest": corpus_digest,
        },
    )
    pointer = write(
        tmp_path / "pointer.json",
        {
            "active": scope.model_dump(mode="json"),
            "corpus_digest": corpus_digest,
            "dense_weight": 0.5,
            "selection": {"dev_digest": "dev", "test_digest": "test"},
        },
    )
    binding = {
        "format": "m42-catalog-binding-v1",
        "catalog_version": "complaints-v3",
        "classifier_mode": "not_configured",
        "classifier_reason": "retained_three_leaf_model_not_calibrated_for_six_leaf",
        "corpus": str(corpus),
        "cascade_policy": policy,
        "dev": str(asset_path("m42_catalog_v3_dev.json")),
        "test": str(asset_path("m42_catalog_v3_test.json")),
        "dev_digest": "dev",
        "test_digest": "test",
    }
    path = tmp_path / "binding.json"
    data = {
        "catalog_binding": write(path, binding),
        "dense_pointer": pointer,
        "files": {
            str(p): "fingerprinted"
            for p in [
                path,
                *map(
                    Path,
                    (
                        binding["corpus"],
                        binding["cascade_policy"],
                        binding["dev"],
                        binding["test"],
                    ),
                ),
            ]
        },
    }
    return data, binding, path


def test_old_release_has_no_implicit_catalog_upgrade():
    assert catalog_binding({}, "complaints-v1") is None
    with pytest.raises(ConfigurationError, match="explicit catalog"):
        catalog_binding({}, "complaints-v3")


def test_bound_six_leaf_assets_are_accepted(assets):
    data, binding, _ = assets
    assert catalog_binding(data, "complaints-v3") == binding


@pytest.mark.parametrize(
    "field,value",
    [
        ("catalog_version", "complaints-v1"),
        ("classifier_mode", "fasttext"),
        ("dev_digest", "other-dev"),
        ("test_digest", "other-test"),
    ],
)
def test_binding_rejects_catalog_classifier_and_frozen_selection_drift(assets, field, value):
    data, binding, path = assets
    binding[field] = value
    write(path, binding)
    with pytest.raises(ConfigurationError):
        catalog_binding(data, "complaints-v3")


def test_binding_requires_every_consumed_asset_fingerprint(assets):
    data, binding, _ = assets
    del data["files"][binding["cascade_policy"]]
    with pytest.raises(ConfigurationError, match="fingerprints"):
        catalog_binding(data, "complaints-v3")

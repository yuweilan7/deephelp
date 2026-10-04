from collections import Counter

import pytest
from pydantic import ValidationError

from deephelp_tools.evaluation.samples import (
    BusinessFixtures,
    SampleCorpus,
    load_business_fixtures,
    load_corpus,
    validate_corpus,
)

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def corpus():
    # Frozen DTOs contain tuples; mutations below always use a fresh model_dump.
    return load_corpus()


@pytest.fixture(scope="module")
def business():
    return load_business_fixtures()


def test_fixed_corpus_labels_and_business_fixture_references_are_consistent(corpus, business):
    validate_corpus(corpus, business)
    assert len(corpus.cases) == 36
    assert Counter(case.split for case in corpus.cases) == {
        "reference": 6,
        "dev": 12,
        "regression": 18,
    }
    assert len({case.variant_group for case in corpus.cases}) == 18
    assert Counter(order.discount_status for order in business.orders).keys() >= {
        "applied",
        "missing",
        "not_eligible",
    }
    assert {coupon.status for coupon in business.coupons} == {
        "usable",
        "expired",
        "threshold_not_met",
    }
    assert any(not order.activity_ids for order in business.orders)
    assert SampleCorpus.model_validate_json(corpus.model_dump_json()) == corpus
    assert BusinessFixtures.model_validate_json(business.model_dump_json()) == business


@pytest.mark.parametrize(
    "change", ["variant_split", "source_split", "duplicate_case", "not_synthetic"]
)
def test_corpus_rejects_leakage_duplicates_and_real_data(corpus, change):
    data = corpus.model_dump(mode="json")
    if change == "variant_split":
        data["cases"][1]["split"] = "dev"
    elif change == "source_split":
        data["cases"][2]["source_group"] = data["cases"][0]["source_group"]
    elif change == "duplicate_case":
        data["cases"][1]["case_id"] = data["cases"][0]["case_id"]
    else:
        data["cases"][0]["synthetic"] = False
    with pytest.raises(ValidationError):
        SampleCorpus.model_validate(data)


@pytest.mark.parametrize(
    "change,reason",
    [
        pytest.param("source", "this message as source", id="source"),
        pytest.param("excerpt", "exact original text", id="excerpt"),
        pytest.param("raw_text", "exact original text", id="raw_text"),
        pytest.param("parameter", "Tool name/order", id="parameter"),
        pytest.param("owner", "case owner", id="owner"),
        pytest.param("missing_tool", "one read-only lookup", id="missing_tool"),
        pytest.param("missing_slot_list", "match registry", id="missing_slot_list"),
        pytest.param("missing_outcome", "CLARIFY / WAITING_SLOT / tools 0", id="missing_outcome"),
        pytest.param("missing_status", "CLARIFY / WAITING_SLOT / tools 0", id="missing_status"),
        pytest.param("missing_tools", "CLARIFY / WAITING_SLOT / tools 0", id="missing_tools"),
        pytest.param("unknown_tools", "Unknown intent cannot call tools", id="unknown_tools"),
        pytest.param("forbidden_tools", "Cross-owner case", id="forbidden_tools"),
    ],
)
def test_gold_validation_rejects_invented_entities_unsafe_tools_and_bad_labels(
    corpus, business, change, reason
):
    data = corpus.model_dump(mode="json")
    expected = data["cases"][0]["expected"]
    if change == "source":
        expected["entities"][0]["source"]["message_id"] = "unrelated"
    elif change == "excerpt":
        expected["entities"][0]["source"]["excerpt"] = "unrelated"
    elif change == "raw_text":
        data["cases"][0]["message"]["raw_text"] = "unrelated"
    elif change == "parameter":
        expected["tools"][0]["parameters"]["order_id"] = "000001"
    elif change == "owner":
        data["cases"][0]["identity"]["user_id"] = "other-user"
    elif change == "missing_tool":
        expected["tools"] = []
    elif change == "missing_slot_list":
        data["cases"][2]["expected"]["missing_slots"] = []
    elif change in {"missing_outcome", "missing_status", "missing_tools"}:
        field, value = {
            "missing_outcome": ("outcome", "ANSWERED"),
            "missing_status": ("question_status", "RESOLVED"),
            "missing_tools": ("tools", expected["tools"]),
        }[change]
        data["cases"][2]["expected"][field] = value
    elif change == "unknown_tools":
        target = next(c for c in data["cases"] if c["expected"]["intent"] is None)
        target["expected"]["tools"] = expected["tools"]
    else:
        target = next(c for c in data["cases"] if c["expected"]["error_code"] == "FORBIDDEN")
        target["expected"]["tools"] = expected["tools"]
    damaged = SampleCorpus.model_validate(data)
    with pytest.raises(ValueError, match=reason):
        validate_corpus(damaged, business)


def test_business_fixtures_reject_dangling_or_cross_owner_references(business):
    for change in ("owner", "activity", "duplicate"):
        data = business.model_dump(mode="json")
        if change == "owner":
            data["coupons"][0]["identity"]["user_id"] = "other-user"
        elif change == "activity":
            data["orders"][0]["activity_ids"] = ["unknown-activity"]
        else:
            data["orders"].append(data["orders"][0])
        with pytest.raises(ValidationError):
            BusinessFixtures.model_validate(data)

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


def test_fixed_corpus_labels_and_business_fixture_references_are_consistent():
    corpus, business = load_corpus(), load_business_fixtures()
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


@pytest.mark.parametrize("case_index", range(36))
def test_every_gold_case_retains_sources_and_safe_closeout(case_index):
    corpus = load_corpus()
    case = corpus.cases[case_index]
    for entity in case.expected.entities:
        assert entity.source.message_id == case.message.message_id
        assert entity.value in case.message.raw_text
    if case.expected.missing_slots:
        assert (
            case.expected.outcome == "CLARIFY" and case.expected.question_status == "WAITING_SLOT"
        )
        assert case.expected.tools == ()
    if case.expected.error_code == "FORBIDDEN" or case.expected.intent is None:
        assert case.expected.tools == ()
    # This checks annotated gold data; there is no classifier or executing tool here.


@pytest.mark.parametrize(
    "change", ["variant_split", "source_split", "duplicate_case", "not_synthetic"]
)
def test_corpus_rejects_leakage_duplicates_and_real_data(change):
    data = load_corpus().model_dump(mode="json")
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
    "change", ["source", "parameter", "owner", "missing_tool", "missing_slot_list"]
)
def test_gold_validation_rejects_invented_entities_unsafe_tools_and_bad_labels(change):
    data = load_corpus().model_dump(mode="json")
    expected = data["cases"][0]["expected"]
    if change == "source":
        expected["entities"][0]["source"]["message_id"] = "unrelated"
    elif change == "parameter":
        expected["tools"][0]["parameters"]["order_id"] = "000001"
    elif change == "owner":
        data["cases"][0]["identity"]["user_id"] = "other-user"
    elif change == "missing_tool":
        expected["tools"] = []
    else:
        data["cases"][2]["expected"]["missing_slots"] = []
    with pytest.raises(ValueError):
        validate_corpus(SampleCorpus.model_validate(data), load_business_fixtures())


def test_business_fixtures_reject_dangling_or_cross_owner_references():
    for change in ("owner", "activity", "duplicate"):
        data = load_business_fixtures().model_dump(mode="json")
        if change == "owner":
            data["coupons"][0]["identity"]["user_id"] = "other-user"
        elif change == "activity":
            data["orders"][0]["activity_ids"] = ["unknown-activity"]
        else:
            data["orders"].append(data["orders"][0])
        with pytest.raises(ValidationError):
            BusinessFixtures.model_validate(data)

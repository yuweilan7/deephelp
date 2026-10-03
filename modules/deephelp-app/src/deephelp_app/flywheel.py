"""Reviewed synthetic feedback, with separate candidate and per-route gold records."""

import re
from typing import Any, Literal

from pydantic import Field

from deephelp_app.assets import asset_path
from deephelp_app.corpus import digest
from deephelp_app.debug import sanitize
from deephelp_app.domain.models import DTO, IntentCode
from deephelp_app.errors import ConfigurationError
from deephelp_app.text_fingerprint import canonical

Route = Literal["corpus", "fasttext", "rule"]
ROUTES: tuple[Route, ...] = ("corpus", "fasttext", "rule")


class RouteReview(DTO):
    state: Literal["approved", "rejected", "disputed", "withdrawn"]
    label: IntentCode | None = None
    reviewer: str = Field(min_length=1, max_length=128)
    method: Literal["human", "deterministic_evidence"]
    reason: str = Field(min_length=1, max_length=1000)
    phrases: tuple[str, ...] = Field(default=(), max_length=8)
    exclusions: tuple[str, ...] = Field(default=(), max_length=16)
    independent_from_heldout: bool = False


class FeedbackCandidate(DTO):
    candidate_id: str
    run_id: str
    source_group: str = Field(min_length=1, max_length=128)
    variant_group: str = Field(min_length=1, max_length=128)
    revision: int = Field(default=0, ge=0)
    synthetic: Literal[True] = True
    text: str = Field(min_length=1, max_length=2000)
    observation: dict[str, Any]
    reviews: dict[Route, RouteReview] = Field(default_factory=dict)


def neutral(text: str) -> str:
    return canonical(re.sub(r"id_[0-9a-f]{16}", "#", text))


def isolation(candidate: FeedbackCandidate) -> None:
    """Identifier-neutral collisions and declared paraphrase groups are both audited."""
    import json

    from deephelp_app.evaluation.core import DATA, historical_inputs

    if neutral(candidate.text) in {neutral(t) for t in historical_inputs()} | {
        neutral(t["text"])
        for path in (DATA, DATA.with_name("m17_scale_cases.json"))
        for case in json.loads(path.read_text(encoding="utf-8"))
        for t in case["turns"]
    }:
        raise ConfigurationError("Known index/train/dev/test/regression input cannot be recycled")
    groups: set[str] = set()
    for row in json.loads(asset_path("cases.json").read_text(encoding="utf-8"))["cases"]:
        groups.update(str(row[k]) for k in ("source_group", "variant_group"))
    for name in (
        "m13_fasttext.json",
        "m09_dev.json",
        "m09_test.json",
        "m17_cases.json",
        "m17_scale_cases.json",
    ):
        for row in json.loads(asset_path(name).read_text(encoding="utf-8")):
            groups.update(str(row[k]) for k in ("source_group", "variant_group"))
    for line in asset_path("m09_corpus.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        groups.update(row[k] for k in ("source_group", "variant_group"))
    demo = json.loads(DATA.with_name("m18_demo.json").read_text(encoding="utf-8"))
    groups.update(row["id"] for row in demo["release"])
    if neutral(candidate.text) in {neutral(row["text"]) for row in demo["release"]}:
        raise ConfigurationError("Independent release input cannot become training feedback")
    if candidate.source_group in groups or candidate.variant_group in groups:
        raise ConfigurationError("Feedback source/variant group belongs to a frozen dataset")


def capture_candidate(
    run_id: str,
    message: dict[str, Any],
    response: dict[str, Any],
    key: bytes,
    *,
    source_group: str,
    variant_group: str,
) -> FeedbackCandidate:
    if message["identity"] != {"tenant_id": "synthetic-tenant", "user_id": "synthetic-user-a"}:
        raise ConfigurationError("This demonstration accepts only the dedicated synthetic identity")
    if message["channel"] != "m18-collect" or response["run_id"] != run_id:
        raise ConfigurationError("Capture requires an M18 source run, never evaluation input")
    data = sanitize(
        {
            "text": message["raw_text"],
            "entities": response.get("facts", []),
            "decision": response.get("intent_decision"),
            "outcome": response["outcome"],
            "evidence_refs": response.get("evidence_refs", []),
            "tool_call_ids": response.get("tool_call_ids", []),
            "trace_id": response["trace_id"],
            "run_id": run_id,
        },
        key,
    )
    text = str(data.pop("text"))

    def contacts(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: contacts(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [contacts(v) for v in value]
        if isinstance(value, str):
            value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", value)
            return re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[phone]", value)
        return value

    text, data = contacts(text), contacts(data)
    return FeedbackCandidate(
        candidate_id=digest(["m18", run_id]),
        run_id=run_id,
        source_group=source_group,
        variant_group=variant_group,
        text=text,
        observation=data,
    )


def reviewed(candidate: FeedbackCandidate, route: Route, review: RouteReview) -> FeedbackCandidate:
    if review.state == "approved":
        isolation(candidate)
        if not review.independent_from_heldout or not review.label or not review.label.actionable:
            raise ConfigurationError(
                "Approval needs a supported gold and explicit heldout group review"
            )
        if review.method == "deterministic_evidence" and (
            review.reviewer != "synthetic-semantic-evidence-v1"
            or __semantic_label(candidate.text) != review.label.value
        ):
            raise ConfigurationError("Registered deterministic evidence does not prove this label")
        if review.method == "deterministic_evidence":
            import json

            from deephelp_app.assets import asset_path

            sources = json.loads(asset_path("m18_demo.json").read_text(encoding="utf-8"))["collect"]
            if not any(
                row["id"] == candidate.source_group == candidate.variant_group
                and neutral(row["text"]) == neutral(candidate.text)
                and row["label"] == review.label.value
                for row in sources
            ):
                raise ConfigurationError(
                    "Only registered audited synthetic source groups use deterministic promotion"
                )
        if route == "rule":
            if not review.phrases or any(
                not 2 <= len(p) <= 80 or p not in candidate.text for p in review.phrases
            ):
                raise ConfigurationError(
                    "Rules require bounded literal evidence, never generated regex/code"
                )
            if any(not 2 <= len(p) <= 80 for p in review.exclusions):
                raise ConfigurationError("Rule exclusions must be bounded literal data")
    reviews = dict(candidate.reviews)
    if review.state == "approved" and any(
        v.state == "approved" and v.label != review.label for v in reviews.values()
    ):
        # A correction invalidates previous route approvals; history and derived files remain.
        reviews = {k: v.model_copy(update={"state": "disputed"}) for k, v in reviews.items()}
    reviews[route] = review
    return candidate.model_copy(update={"reviews": reviews, "revision": candidate.revision + 1})


def withdrawn(candidate: FeedbackCandidate, reviewer: str, reason: str) -> FeedbackCandidate:
    if not reviewer or not reason:
        raise ConfigurationError("Withdrawal must identify its reviewer and reason")
    return candidate.model_copy(
        update={
            "revision": candidate.revision + 1,
            "reviews": {
                k: v.model_copy(
                    update={"state": "withdrawn", "reviewer": reviewer, "reason": reason}
                )
                for k, v in candidate.reviews.items()
            },
        }
    )


def snapshot(rows: list[FeedbackCandidate]) -> dict[str, Any]:
    sources: dict[str, Any] = {}
    for candidate in rows:
        for review in candidate.reviews.values():
            if review.state != "approved":
                continue
            isolation(candidate)
            sources[candidate.candidate_id] = candidate.model_dump(mode="json")
    return {"format": "m18-review-snapshot-v1", "candidates": sources, "digest": digest(sources)}


def rule_matches(data: dict[str, Any], request: Any) -> list[Any]:
    """Data-only literal predicates, retaining original text spans and source revision."""
    from deephelp_app.domain.models import EntitySource, RuleMatch
    from deephelp_app.text_entity import INJECTION, width_normalize

    if data.get("format") != "m18-literal-rules-v1":
        raise ConfigurationError("Invalid reviewed rule artifact")
    if INJECTION.search(request.raw_text) or re.search(
        r"不是|已恢复|已经可以|已经能用", request.raw_text
    ):
        return []
    result = []
    for raw in data["rules"]:
        review = RouteReview.model_validate(raw["review"])
        if review.state != "approved" or not review.label or not review.label.actionable:
            raise ConfigurationError("Unapproved rule artifact")
        if not review.phrases or any(
            not 2 <= len(p) <= 80 for p in (*review.phrases, *review.exclusions)
        ):
            raise ConfigurationError("Invalid literal rule predicates")
        text = width_normalize(request.raw_text)
        if any(p in text for p in review.exclusions) or not all(p in text for p in review.phrases):
            continue
        start = min(text.index(p) for p in review.phrases)
        end = max(text.index(p) + len(p) for p in review.phrases)
        result.append(
            RuleMatch(
                rule_id="m18-" + raw["candidate_id"][:16],
                rule_version=data["version"],
                candidate_code=review.label,
                priority=5,
                start=start,
                end=end,
                evidence=EntitySource(
                    message_id=request.message_id, excerpt=request.raw_text[start:end]
                ),
            )
        )
    return result


def __semantic_label(text: str) -> str:
    from deephelp_app.evaluation.core import semantic_label

    return semantic_label(text)

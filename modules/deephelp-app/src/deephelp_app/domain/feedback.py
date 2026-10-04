"""Authoritative feedback values and runtime literal predicates; no frozen data."""

import re
from typing import Any, Literal

from pydantic import Field

from deephelp_app.domain.models import DTO, IntentCode
from deephelp_app.domain.text_fingerprint import canonical

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

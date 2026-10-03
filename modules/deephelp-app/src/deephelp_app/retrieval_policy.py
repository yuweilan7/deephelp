"""Validate a published retrieval selection without reopening dev/test inputs.

The publisher still verifies query hashes against the frozen datasets. Startup
checks the immutable selection, scope, corpus, analyzer and budgets; dev/test hashes
are retained as provenance, never used as business data.
"""

import re
from typing import Any

from deephelp_app.corpus import digest
from deephelp_app.domain.models import DenseScope
from deephelp_app.errors import ConfigurationError
from deephelp_app.hybrid import ANALYZER


def validate_published_selection(
    value: dict[str, Any], scope: DenseScope, index_digest: str, top_k: int
) -> None:
    payload = {k: v for k, v in value.items() if k != "selection_digest"}
    if (
        value.get("format") != "m09-dev-selection-v1"
        or value.get("scope") != scope.model_dump(mode="json")
        or value.get("index_digest") != index_digest
        or any(
            not isinstance(value.get(key), str) or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None
            for key in ("dev_digest", "test_digest")
        )
        or value.get("candidate_budget") != top_k
        or isinstance(top_k, bool)
        or not 1 <= top_k <= 20
        or value.get("analyzer") != ANALYZER
        or value.get("selection_digest") != digest(payload)
        or isinstance(value.get("dense_weight"), bool)
        or value.get("dense_weight") not in {0.25, 0.5, 0.75}
    ):
        raise ConfigurationError("Published selection/corpus/scope/budget mismatch")

"""Explicit M02 frozen corpus preview."""

import hashlib

from deephelp_app.application.corpus import CorpusPreview, validate_records
from deephelp_tools.assets import asset_path


def frozen_preview() -> CorpusPreview:
    from deephelp_tools.evaluation.samples import load_corpus

    """M02 gold-null cases remain query-only and are evaluated separately by future classifiers."""
    cases = [c for c in load_corpus().cases if c.expected.intent is not None]
    raw = [
        {
            "doc_id": c.case_id,
            "content": c.message.raw_text,
            "intent_code": c.expected.intent,
            "split": c.split,
            "source_group": c.source_group,
            "variant_group": c.variant_group,
            "synthetic": True,
            "metadata": {"scenario": c.scenario},
        }
        for c in cases
    ]
    source = asset_path("cases.json").read_bytes()
    return validate_records(list(enumerate(raw, 1)), hashlib.sha256(source).hexdigest())

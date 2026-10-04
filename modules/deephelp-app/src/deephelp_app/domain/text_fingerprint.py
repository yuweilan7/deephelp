"""Identifier-neutral text fingerprint shared by feedback and dataset audit."""

import re
import unicodedata


def canonical(text: str) -> str:
    # Also audit identifier-only rewrites: changing an order is not an independent case.
    value = unicodedata.normalize("NFKC", text).lower()
    value = re.sub(r"(?:demo|coupon|sku|activity)[-_a-z0-9]+|[0-9]+", "#", value)
    return re.sub(r"[\s，。；、：,.!?！？]+", "", value)

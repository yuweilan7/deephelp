"""Summarize existing structural observations without triggering model requests."""

from typing import Any

TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens")


def api_records(
    diagnostics: list[dict[str, object]],
    *,
    phase: str,
    sample: str | None = None,
    previous: list[dict[str, object]] | None = None,
) -> list[dict[str, Any]]:
    # Compare record identities, not offsets: the gateway keeps a bounded tail.
    seen = {id(row) for row in previous or []}
    return [
        {**row, "phase": phase, "sample": sample}
        for row in diagnostics
        if "path" in row and id(row) not in seen
    ]


def summarize_usage(
    records: list[dict[str, Any]], *, attempts: int | None = None
) -> dict[str, Any]:
    def counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {"responses_observed": len(rows)}
        for field in TOKEN_FIELDS:
            values = [(row.get("api_usage") or {}).get(field) for row in rows]
            known = [n for n in values if type(n) is int and n >= 0]
            result[field] = sum(known) if len(known) == len(values) and values else None
            result["observed_" + field] = sum(known) if known else None
        return result

    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in records:
        groups.setdefault((row["phase"], row.get("model"), row.get("sample")), []).append(row)
    result = counts(records)
    result.update(
        api_attempts=attempts,
        unobserved_attempts=max(0, attempts - len(records)) if attempts is not None else None,
        by_phase_model_sample=[
            {"phase": key[0], "model": key[1], "sample": key[2], **counts(rows)}
            for key, rows in groups.items()
        ],
        note=(
            "API counts only; null means unknown. Observed subtotals may be incomplete. "
            "Reasoning may be included in output; never add it again. "
            "Budget reservations and configured cost estimates are separate."
        ),
    )
    if attempts is None or attempts != len(records):
        for field in TOKEN_FIELDS:
            result[field] = None
    return result

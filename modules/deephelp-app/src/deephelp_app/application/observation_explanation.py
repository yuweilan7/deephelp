"""解释实际观测的用量与时间关系，供原run只读展示。

按原标识去重GENERATION，父级汇总不再相加；冲突或缺失字段保留未知。
起止时间只能证明重叠与跨度，不能推断未采集的CPU、排队或线程运行时间。
"""

from math import isfinite
from typing import Any


def observation_summary(spans: list[dict[str, Any]], status: str) -> dict[str, Any]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    conflicts = set()
    for span in spans:
        identifier = (span["trace_id"], span["span_id"])
        if identifier in unique and unique[identifier] != span:
            conflicts.add(identifier)
        unique[identifier] = span
    generations = [(key, span) for key, span in unique.items() if span["type"] == "GENERATION"]
    known, unknown, subtotal = 0, 0, 0
    for key, span in generations:
        usage = span.get("usage")
        if key in conflicts or not isinstance(usage, dict):
            unknown += 1
            continue
        # Native input/output are disjoint. Reasoning is already included in output;
        # parent summaries and additional usage keys must not be added again.
        counts = [usage.get("input"), usage.get("output")]
        known_counts = [
            n for n in counts if isinstance(n, int) and not isinstance(n, bool) and n >= 0
        ]
        if len(known_counts) == 2:
            subtotal += sum(known_counts)
            known += 1
        else:
            unknown += 1
    envelopes = []
    for trace in dict.fromkeys(key[0] for key in unique):
        rows = [s for (t, _), s in unique.items() if t == trace]
        intervals = [(int(s["start_unix_ns"]), s.get("duration_ms")) for s in rows]
        valid = [
            (start, d)
            for start, d in intervals
            if isinstance(d, (int, float)) and not isinstance(d, bool) and isfinite(d) and d >= 0
        ]
        envelopes.append(
            {
                "trace_id": trace,
                "observed_extent_ms": (
                    max(start + d * 1_000_000 for start, d in valid)
                    - min(start for start, _ in valid)
                )
                / 1_000_000
                if valid
                else None,
                "complete_intervals": len(valid) == len(rows),
            }
        )
    return {
        "scope": "all observations returned by the bounded platform read; not only this UI page",
        "unique_generations": len(generations),
        "duplicates_removed": len(spans) - len(unique),
        "conflicting_observations": len(conflicts),
        "known_usage_attempts": known,
        "unknown_usage_attempts": unknown,
        "known_token_subtotal": subtotal,
        "actual_total_tokens": subtotal
        if status == "available" and not unknown and not conflicts and generations
        else None,
        "trace_extents": envelopes,
        "queue_ms": None,
        "cpu_ms": None,
        "notes": [
            "每个 trace/span 的 GENERATION 只计一次，父级汇总不相加，reasoning 不再加到 output。",
            "已知小计不是完整用量；平台不全、缺失 usage 或冲突时总量未知。预留和费用估算看原预算。",
            "时间范围只包围已观察节点；重叠不求和，不是请求总耗时或 CPU 时间。",
            "pause/resume 各有运行，通过持久前序关联；人工审批等待不能并入执行耗时。",
        ],
    }

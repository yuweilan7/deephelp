"""Frozen synthetic retrieval comparison; dev alone selects the weight."""

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from deephelp_app.corpus import CorpusPreview, digest, read_corpus
from deephelp_app.domain.models import (
    DTO,
    DemandType,
    DenseResult,
    DenseScope,
    ExtractionLayerStat,
    IntentCode,
    TextEntityResult,
)
from deephelp_app.errors import ConfigurationError
from deephelp_app.execution import ExecutionBudget
from deephelp_app.hybrid import ANALYZER
from deephelp_app.intent import IntentService
from deephelp_app.ports import ChatPort
from deephelp_app.text_entity import TextPolicy, clean_text


class RetrievalQuery(DTO):
    query_id: str
    text: str
    category: str
    gold: IntentCode | None
    source_group: str
    variant_group: str


def dataset() -> tuple[CorpusPreview, dict[str, list[RetrievalQuery]]]:
    base = files("deephelp_app.sample_data")
    corpus = read_corpus(Path(str(base.joinpath("m09_corpus.jsonl"))))
    corpus.require_valid()
    queries = {
        split: [
            RetrievalQuery.model_validate(row)
            for row in json.loads(base.joinpath(f"m09_{split}.json").read_text(encoding="utf-8"))
        ]
        for split in ("dev", "test")
    }
    seen = {r.content.strip() for r in corpus.records}
    groups = {r.source_group for r in corpus.records} | {r.variant_group for r in corpus.records}
    ids = {r.doc_id for r in corpus.records}
    for rows in queries.values():
        for q in rows:
            if (
                not q.text.strip()
                or len(q.text) > 2000
                or q.text.strip() in seen
                or q.query_id in ids
                or q.source_group in groups
                or q.variant_group in groups
            ):
                raise ValueError("Frozen queries duplicate/leak index or another split")
            seen.add(q.text.strip())
            ids.add(q.query_id)
            groups.update((q.source_group, q.variant_group))
    return corpus, queries


def query_digest(rows: list[RetrievalQuery]) -> str:
    return digest([q.model_dump(mode="json") for q in rows])


def query_text(query: RetrievalQuery) -> str:
    return clean_text(query.text, TextPolicy()).cleaned_text


def ranking(q: RetrievalQuery, result: DenseResult) -> dict[str, object]:
    codes = [c.intent_code for c in result.candidates]
    doc_codes = [h.intent_code for h in result.hits]
    rank = codes.index(q.gold) + 1 if q.gold in codes else None
    return {
        "eligible": q.gold is not None,
        "recall_1": int(q.gold is not None and q.gold in doc_codes[:1]),
        "recall_3": int(q.gold is not None and q.gold in doc_codes[:3]),
        "candidate_recall_1": int(q.gold is not None and q.gold in codes[:1]),
        "mrr": 1 / rank if rank else 0.0,
        "top1": doc_codes[0].value if doc_codes else None,
    }


def metrics(observations: list[dict[str, Any]], mode: str) -> dict[str, object]:
    relevant = [o["routes"][mode] for o in observations if o["routes"][mode]["eligible"]]
    value: dict[str, object] = {"queries": len(observations), "labelled_queries": len(relevant)}
    for key in ("recall_1", "recall_3", "candidate_recall_1", "mrr"):
        value[key] = sum(float(r[key]) for r in relevant) / len(relevant) if relevant else None
    classified = [
        o["routes"][mode] for o in observations if "classification_correct" in o["routes"][mode]
    ]
    value["classification_queries"] = len(classified)
    value["classification_accuracy"] = (
        sum(r["classification_correct"] for r in classified) / len(classified)
        if classified
        else None
    )
    latency = sorted(float(o["routes"][mode]["server_elapsed_ms"]) for o in observations)
    value["server_latency_p50_ms"] = latency[len(latency) // 2] if latency else None
    value["server_latency_max_ms"] = max(latency) if latency else None
    value["ranking_failures"] = [
        o["query_id"]
        for o in observations
        if o["routes"][mode]["eligible"] and not o["routes"][mode]["recall_1"]
    ]
    value["classification_failures"] = [
        o["query_id"]
        for o in observations
        if o["routes"][mode].get("classification_correct") is False
    ]
    return value


def choose_weight(dev: dict[float, dict[str, object]]) -> float:
    # Fixed order of objectives; no held-out labels, test report or post-hoc tuning input.
    return max(
        dev,
        key=lambda w: (
            float(str(dev[w]["candidate_recall_1"])),
            float(str(dev[w]["mrr"])),
            -abs(w - 0.5),
            -w,
        ),
    )


def validate_selection(
    value: dict[str, Any],
    scope: DenseScope,
    index_digest: str,
    queries: dict[str, Any],
    top_k: int,
) -> None:
    payload = {k: v for k, v in value.items() if k != "selection_digest"}
    if (
        value.get("format") != "m09-dev-selection-v1"
        or value.get("scope") != scope.model_dump(mode="json")
        or value.get("index_digest") != index_digest
        or value.get("dev_digest") != query_digest(queries["dev"])
        or value.get("test_digest") != query_digest(queries["test"])
        or value.get("candidate_budget") != top_k
        or value.get("analyzer") != ANALYZER
        or value.get("selection_digest") != digest(payload)
        or isinstance(value.get("dense_weight"), bool)
        or value.get("dense_weight") not in {0.25, 0.5, 0.75}
    ):
        raise ConfigurationError("Frozen selection/corpus/query/scope/budget mismatch")


async def classify(
    q: RetrievalQuery, result: DenseResult, model: ChatPort, budget: ExecutionBudget
) -> dict[str, object]:
    class RecordedRetriever:
        async def retrieve(
            self, text: str, scope: DenseScope, budget: ExecutionBudget, *, top_k: int = 3
        ) -> DenseResult:
            if text != query_text(q) or scope != result.scope:
                raise ValueError(
                    "Classification must consume the already-executed same-query route"
                )
            return result

    prepared = TextEntityResult(
        message_id=q.query_id,
        clean=clean_text(q.text, TextPolicy()),
        demand_type=DemandType.MAIN,
        layers=[ExtractionLayerStat(layer="regex", elapsed_ms=0, accepted=0)],
    )
    decision = await IntentService(model, RecordedRetriever(), result.scope).recognize(
        prepared, budget
    )
    code = decision.final_code.value if decision.final_code else None
    return {
        "final_code": code,
        "decision": decision.decision.value,
        "reason": decision.reason_code,
        "classification_correct": code == (q.gold.value if q.gold else None),
    }

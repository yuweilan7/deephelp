"""独立分类实验的冻结合同与配对统计，不执行模型或业务。

首条消息是观察单位；完整分母包含错误/缺失，拒识与叶子分类分开。
Wilson区间与配对符号检验解释小样本，不能把一次AI合成测试外推为客户收益。
"""

import math
from collections import Counter
from typing import Any, Literal

from pydantic import Field

from deephelp_app.application.corpus import digest
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.models.common import DTO
from deephelp_tools.evaluation.quality.independent_data import LABELS, verify


class Variant(DTO):
    route: Literal["hybrid", "dense"]
    system_prompt: str = Field(min_length=1, max_length=16384)


class Experiment(DTO):
    """服务器预先冻结的单因素实验；浏览器只能选择登记ID。

    files与source绑定确切字节；一次repeat在派发前固定，不按结果追加尝试。
    holdout只允许review批准后使用，后续机制负责消费一次锁定结果。
    """

    format: Literal["m45-paired-contract-v1"] = "m45-paired-contract-v1"
    dataset_id: str
    freeze_hash: str
    split: Literal["dev", "holdout"]
    target: Literal["intent_cascade_first_turn"] = "intent_cascade_first_turn"
    repeats: Literal[1] = 1
    factor: Literal["retrieval_route", "fallback_prompt"]
    baseline: Variant
    candidate: Variant
    selection_hash: str
    files: dict[str, str]
    source: dict[str, str]
    endpoint_hashes: dict[str, str] = Field(default_factory=dict)
    review_hash: str | None = None

    def require_factor(self) -> None:
        a, b = self.baseline, self.candidate
        if self.factor == "retrieval_route":
            valid = a.route != b.route and a.system_prompt == b.system_prompt
        else:
            valid = a.route == b.route and a.system_prompt != b.system_prompt
        if not valid or (self.split == "holdout" and self.review_hash is None):
            raise ConfigurationError("Single factor / reviewed holdout contract required")

    @property
    def fingerprint(self) -> str:
        return digest(self.model_dump(mode="json"))


def selected(snapshot: dict[str, Any], split: str) -> list[dict[str, Any]]:
    """只在评分器读取相应split金标，选择集合与输入/金标共同进入冻结合同。"""
    verify(snapshot)
    result = []
    for case in snapshot["intake"]["cases"]:
        if snapshot["intake"]["splits"][case["case_id"]] != split:
            continue
        turn = case["turns"][0]
        result.append(
            dict(case_id=case["case_id"], **turn, gold=snapshot["gold"][turn["turn_id"]]["label"])
        )
    if not result:
        raise ConfigurationError("Empty selected split is not an evaluation")
    return result


def label(decision: dict[str, Any]) -> str:
    code = decision.get("final_code")
    value = code or ("multiple" if "multiple" in str(decision.get("reason")) else "unknown")
    if value not in LABELS:
        raise ConfigurationError("Classifier returned an unregistered label")
    return str(value)


def wilson(correct: int, n: int) -> list[float] | None:
    if not n:
        return None
    z, p = 1.959963984540054, correct / n
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [max(0.0, center - radius), min(1.0, center + radius)]


def metrics(rows: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    """每类支持数固定；undefined precision/recall为None，错误不从分母排除。"""
    pairs = [(r["gold"], r.get(mode, {}).get("label", "__ERROR__")) for r in rows]
    confusion = {g: dict(Counter(p for gold, p in pairs if gold == g)) for g in sorted(LABELS)}
    per_label = {}
    for g in sorted(LABELS):
        support = sum(gold == g for gold, _ in pairs)
        predicted = sum(p == g for _, p in pairs)
        tp = sum(gold == p == g for gold, p in pairs)
        per_label[g] = dict(
            support=support,
            predicted=predicted,
            true_positive=tp,
            precision=tp / predicted if predicted else None,
            recall=tp / support if support else None,
            f1=2 * tp / (support + predicted) if support + predicted else None,
        )
    correct = sum(g == p for g, p in pairs)
    eligible = [
        r.get(mode, {}).get("ranking", {}) for r in rows if r["gold"] not in {"unknown", "multiple"}
    ]
    latency = sorted(float(r[mode]["elapsed_ms"]) for r in rows if "elapsed_ms" in r.get(mode, {}))
    return dict(
        denominator=len(rows),
        correct=correct,
        accuracy=correct / len(rows) if rows else None,
        accuracy_wilson95=wilson(correct, len(rows)),
        errors=sum(p == "__ERROR__" for _, p in pairs),
        confusion=confusion,
        per_label=per_label,
        macro_f1=sum(v["f1"] or 0 for v in per_label.values() if v["support"])
        / sum(bool(v["support"]) for v in per_label.values())
        if pairs
        else None,
        reject_denominator=sum(g in {"unknown", "multiple"} for g, _ in pairs),
        reject_correct=sum(g == p and g in {"unknown", "multiple"} for g, p in pairs),
        supported_denominator=len(eligible),
        retrieval={
            k: sum(float(r.get(k, 0)) for r in eligible) / len(eligible) if eligible else None
            for k in ("recall_1", "recall_3", "mrr")
        },
        latency_observed=len(latency),
        latency_p50_ms=latency[len(latency) // 2] if latency else None,
        latency_max_ms=max(latency) if latency else None,
    )


def report(spec: Experiment, rows: list[dict[str, Any]], expected: list[str]) -> dict[str, Any]:
    """运行完成、收益与统计置信度分别表达；保留每个改善/退化/错误对应的原观察。"""
    ids = [r["turn_id"] for r in rows]
    if ids != expected or len(set(ids)) != len(ids):
        raise ConfigurationError("Pair rows differ from frozen full denominator/order")
    m = {mode: metrics(rows, mode) for mode in ("baseline", "candidate")}
    improved, regressed = [], []
    for r in rows:
        a = r.get("baseline", {}).get("label") == r["gold"]
        b = r.get("candidate", {}).get("label") == r["gold"]
        if b and not a:
            improved.append(r["turn_id"])
        if a and not b:
            regressed.append(r["turn_id"])
    n, low = len(improved) + len(regressed), min(len(improved), len(regressed))
    sign_p = min(1.0, 2 * sum(math.comb(n, i) for i in range(low + 1)) / 2**n) if n else 1.0
    complete = all(mode in r and "error" not in r[mode] for r in rows for mode in m)
    return dict(
        format="m45-paired-report-v1",
        status="COMPLETE" if complete else "PARTIAL",
        experiment_fingerprint=spec.fingerprint,
        contract=spec.model_dump(mode="json"),
        acceptance_scope=spec.target,
        source_kind="independent_ai_synthetic",
        rows=rows,
        routes={k: dict(metrics=v) for k, v in m.items()},
        paired=dict(
            improved=improved,
            regressed=regressed,
            unchanged=len(rows) - n,
            accuracy_delta=(m["candidate"]["correct"] - m["baseline"]["correct"]) / len(rows),
            discordant=n,
            exact_sign_p_two_sided=sign_p,
        ),
        interpretation=(
            "One preset repeat; small AI synthetic sample. COMPLETE is execution, "
            "not quality gain. No business resolution, multi-turn or slot claim."
        ),
    )

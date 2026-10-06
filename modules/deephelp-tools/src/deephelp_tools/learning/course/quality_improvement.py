"""一轮改进的离线阅读入口，共用纯审核/Prompt资产，不读取私有holdout或发请求。

先冻结收益/守卫，再生成候选，相当于Java不可变审批DTO加版本化资源；
模型建议只有通过代码与原发布门禁才可生效，平均收益不能抵消关键拒识退化。
"""

from typing import Any

from deephelp_tools.evaluation.quality.improvement import Criteria, quality_checks
from deephelp_tools.evaluation.quality.paired import Experiment, Variant, report


async def quality_improvement_demo() -> dict[str, object]:
    """练习答案是保留baseline：零提升或一次关键退化均不能满足事前标准。"""
    criteria = Criteria()
    spec = Experiment(
        dataset_id="teaching",
        freeze_hash="teaching",
        split="dev",
        factor="retrieval_route",
        baseline=Variant(route="hybrid", system_prompt="same"),
        candidate=Variant(route="dense", system_prompt="same"),
        selection_hash="teaching",
        files={},
        source={},
    )
    rows: list[dict[str, Any]] = [
        dict(turn_id=str(i), gold=g, baseline=dict(label=g), candidate=dict(label=g))
        for i, g in enumerate(["unknown"] * 3 + ["multiple"] * 3 + ["DISCOUNT_MISSING"] * 10)
    ]
    zero = report(spec, rows, [r["turn_id"] for r in rows])
    rows[0]["candidate"] = {"label": "DISCOUNT_MISSING"}
    regression = report(spec, rows, [r["turn_id"] for r in rows])
    return dict(
        criteria=criteria.model_dump(mode="json"),
        zero_gain_checks=quality_checks(criteria, zero),
        regression_checks=quality_checks(criteria, regression),
        adoption_requires_original_full_release=True,
        remote_model_calls=0,
        database_calls=0,
        quality_claim=False,
        reading_order=[
            "improvement.review",
            "improvement.revise",
            "paired_runtime.execute",
            "improvement.close",
            "release_prepare.prepare_manifest",
        ],
    )

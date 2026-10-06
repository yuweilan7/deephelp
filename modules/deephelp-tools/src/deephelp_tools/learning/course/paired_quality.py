"""离线配对统计实验，复用正式评分器；Java可类比不可变实验DTO与持久任务日志。

示例故意让候选一题改善、一题退化：平均值相等并不表示没有行为变化。
本课程不使用真实holdout、网络或数据库，不能作为真实模型质量证据。
"""

from deephelp_tools.evaluation.quality.paired import Experiment, Variant, report


async def paired_quality_demo() -> dict[str, object]:
    """观察错误进入分母、配对变化及小样本不确定性，固定动作无需随机Mock模型。"""
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
    rows = [
        dict(
            turn_id="a",
            gold="unknown",
            baseline=dict(label="unknown"),
            candidate=dict(label="multiple"),
        ),
        dict(
            turn_id="b",
            gold="multiple",
            baseline=dict(error="ProtocolFailure"),
            candidate=dict(label="multiple"),
        ),
    ]
    result = report(spec, rows, ["a", "b"])
    return dict(remote_model_calls=0, database_calls=0, quality_claim=False, **result)

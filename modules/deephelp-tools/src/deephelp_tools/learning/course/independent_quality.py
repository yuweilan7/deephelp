"""原第14章的泄漏/盲标离线实验：共用真实冻结核心，远程模型和PG均0。"""

from collections.abc import Callable
from typing import Any

from deephelp_app.domain.errors import ConfigurationError
from deephelp_tools.evaluation.quality.independent_data import (
    Annotation,
    Intake,
    annotate,
    freeze,
    receive,
    summary,
    verify,
)


def teaching_intake() -> Intake:
    """三个同作者教学例只用于机制反例，不声称它们是M45独立质量样本。"""
    texts = [
        "折扣那一栏空着，结算款却一分不少",
        "券在钱包里可见，付款页总提示不能抵扣",
        "请帮我追踪包裹下一站在哪里",
    ]
    return Intake.model_validate(
        dict(
            author_id="course-author",
            source_kind="independent_ai_synthetic",
            source_receipt="Teaching declaration only; no independent quality claim",
            permission_basis="Synthetic mechanism fixture",
            process=dict(
                existing_samples_seen=False,
                tested_outputs_seen=False,
                provided_context="Teaching fixture definitions",
            ),
            cases=[
                dict(
                    case_id=f"case{i}",
                    source_group=f"source{i}",
                    duplicate_group=f"group{i}",
                    scenario="teaching",
                    turns=[dict(turn_id=f"turn{i}", text=text)],
                )
                for i, text in enumerate(texts)
            ],
            splits=dict(case0="train", case1="dev", case2="holdout"),
        )
    )


def teaching_annotation(row: dict[str, Any], reviewer: str) -> Annotation:
    return Annotation.model_validate(
        dict(
            reviewer_id=reviewer,
            source_receipt="Teaching blind receipt",
            input_hash=row["input_hash"],
            process=dict(
                existing_samples_seen=False,
                tested_outputs_seen=False,
                provided_context="Input only",
            ),
            labels=[
                dict(
                    case_id=f"case{i}",
                    turn_id=f"turn{i}",
                    label=label,
                    basis="Explicit synthetic meaning",
                )
                for i, label in enumerate(("DISCOUNT_MISSING", "COUPON_UNUSABLE", "unknown"))
            ],
        )
    )


async def independent_quality_demo() -> dict[str, object]:
    body = teaching_intake()
    row = receive(body, {}, [])
    rejected = []
    leaked = body.model_dump(mode="json")
    leaked["cases"][2]["turns"][0]["text"] = leaked["cases"][0]["turns"][0]["text"]
    operations: tuple[Callable[[], object], ...] = (
        lambda: receive(Intake.model_validate(leaked), {}, []),
        lambda: annotate(row, teaching_annotation(row, "course-author"), {}),
        lambda: freeze(row),
    )
    for operation in operations:
        try:
            operation()
        except ConfigurationError as exc:
            rejected.append(str(exc))
    for reviewer in ("blind-a", "blind-b"):
        row = annotate(row, teaching_annotation(row, reviewer), {})
    row = freeze(row)
    verify(row["frozen"])
    return dict(
        rejected=rejected,
        frozen=summary(row),
        remote_model_calls=0,
        database_calls=0,
        quality_claim=False,
        lesson="Two declarations demonstrate guards, not independent customer quality",
    )

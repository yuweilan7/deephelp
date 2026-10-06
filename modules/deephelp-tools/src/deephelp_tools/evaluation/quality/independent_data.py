"""独立评测数据的纯状态变换；输入与金标分开接收，不读取被测答案。

PG/API 和离线课程共用这些函数。作者、标注者的独立性是可追溯流程声明，
代码负责完整性、身份分离、盲审、泄漏与冻结守卫，不能证明人的诚实或客户泛化。
阅读顺序：receive → annotate → adjudicate → freeze → verify；锁/事务由仓库拥有。
"""

import copy
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from deephelp_app.application.corpus import digest
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.models.common import DTO, Identifier
from deephelp_app.domain.models.intents import IntentCode
from deephelp_app.domain.text_fingerprint import canonical

LABELS = tuple(c.value for c in IntentCode if c.actionable) + ("unknown", "multiple")
Split = Literal["train", "dev", "holdout"]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class BlindProcess(DTO):
    """来源/标注收据的明确声明；接收器另外记录真实操作主体和时间。"""

    existing_samples_seen: Literal[False]
    tested_outputs_seen: Literal[False]
    provided_context: str = Field(min_length=1, max_length=1000)


class InputTurn(DTO):
    turn_id: Identifier
    text: str = Field(min_length=1, max_length=2000, pattern=r"\S")


class InputCase(DTO):
    case_id: Identifier
    source_group: Identifier
    duplicate_group: Identifier
    scenario: str = Field(min_length=1, max_length=200)
    turns: tuple[InputTurn, ...] = Field(min_length=1, max_length=10)


class Intake(DTO):
    author_id: Identifier
    source_kind: Literal[
        "independent_ai_synthetic", "independent_human_synthetic", "authorized_customer"
    ]
    source_receipt: str = Field(min_length=1, max_length=2000)
    permission_basis: str = Field(min_length=1, max_length=2000)
    process: BlindProcess
    cases: tuple[InputCase, ...] = Field(min_length=3, max_length=1000)
    splits: dict[Identifier, Split] = Field(min_length=3, max_length=1000)


class Gold(DTO):
    case_id: Identifier
    turn_id: Identifier
    label: str = Field(min_length=1, max_length=80)
    basis: str = Field(min_length=1, max_length=1000)


class Annotation(DTO):
    reviewer_id: Identifier
    source_receipt: str = Field(min_length=1, max_length=2000)
    input_hash: Hash
    process: BlindProcess
    labels: tuple[Gold, ...] = Field(min_length=1, max_length=10000)


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise ConfigurationError(reason)


def neutral(text: str) -> str:
    """编号/大小写/标点中立指纹，不靠不同订单号制造独立样本。"""
    return re.sub(r"[a-z]*\d+", "#", canonical(text).lower())


def near(a: str, b: str) -> bool:
    a, b = neutral(a), neutral(b)
    if a == b:
        return True
    left = {a[i : i + 3] for i in range(max(1, len(a) - 2))}
    right = {b[i : i + 3] for i in range(max(1, len(b) - 2))}
    return len(left & right) / len(left | right) >= 0.8


def receive(body: Intake, actor: dict[str, Any], historical: list[str]) -> dict[str, Any]:
    """整批验证后才生成聚合。隐私原文不落盘；拒绝后可由来源先脱敏再重新接收。"""
    rows = [c.model_dump(mode="json") for c in body.cases]
    ids = [c["case_id"] for c in rows]
    turns = [t for c in rows for t in c["turns"]]
    require(
        len(ids) == len(set(ids)) and set(ids) == set(body.splits), "Case/split identity differs"
    )
    require(len(turns) == len({t["turn_id"] for t in turns}), "Duplicate turn identity")
    require(set(body.splits.values()) == {"train", "dev", "holdout"}, "All three splits required")
    groups: dict[str, str] = {}
    previous: list[tuple[str, str, str]] = []
    for c in rows:
        split = body.splits[c["case_id"]]
        for name in ("source_group", "duplicate_group"):
            key = name + ":" + c[name]
            require(
                key not in groups or groups[key] == split,
                "Conversation/duplicate group split leakage",
            )
            groups[key] = split
        context = ""
        for t in c["turns"]:
            text = t["text"]
            context += "\n" + text
            require(
                not re.search(
                    r"https?://|[\w.+-]+@[\w.-]+\.[a-zA-Z]+|(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)\d{17}[\dXx](?!\d)",
                    text,
                ),
                "Source must remove contact/personal identifiers before intake",
            )
            for old, old_split, old_case in previous:
                if near(context, old):
                    require(old_split == split, "Near duplicate leaks across splits")
                    require(
                        old_case == c["case_id"]
                        or any(
                            c[g] == next(x for x in rows if x["case_id"] == old_case)[g]
                            for g in ("source_group", "duplicate_group")
                        ),
                        "Near duplicates require a shared declared group",
                    )
            if split == "holdout":
                require(
                    not any(near(context, old) for old in historical),
                    "Holdout overlaps historical development inputs",
                )
            previous.append((context, split, c["case_id"]))
    payload = body.model_dump(mode="json")
    return dict(
        format="m45-independent-data-v1",
        dataset_id=digest(payload),
        revision=0,
        status="REVIEWING",
        intake=payload,
        input_hash=digest(rows),
        imported_by=actor,
        imported_at=datetime.now(UTC).isoformat(),
        annotations={},
        resolutions={},
        historical_hash=digest(historical),
        frozen=None,
    )


def annotate(row: dict[str, Any], body: Annotation, actor: dict[str, Any]) -> dict[str, Any]:
    """两个不同作者之外的完整盲标；不向标注者返回其他标签或被测输出。"""
    value = copy.deepcopy(row)
    require(value["status"] == "REVIEWING", "Frozen labels cannot change")
    require(body.input_hash == row["input_hash"], "Annotation input hash differs")
    require(body.reviewer_id != row["intake"]["author_id"], "Author cannot approve own gold")
    require(body.reviewer_id not in value["annotations"], "Reviewer receipt already recorded")
    expected = {(c["case_id"], t["turn_id"]) for c in row["intake"]["cases"] for t in c["turns"]}
    require(
        len(body.labels) == len(expected)
        and {(g.case_id, g.turn_id) for g in body.labels} == expected,
        "Blind annotation must cover every input exactly once",
    )
    require(all(g.label in LABELS for g in body.labels), "Unregistered gold label")
    receipt = body.model_dump(mode="json")
    receipt.update(submitted_by=actor, submitted_at=datetime.now(UTC).isoformat())
    value["annotations"][body.reviewer_id] = receipt
    return value


def disagreements(row: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    votes: dict[str, list[dict[str, Any]]] = {}
    for reviewer, receipt in row["annotations"].items():
        for g in receipt["labels"]:
            votes.setdefault(g["turn_id"], []).append(dict(reviewer_id=reviewer, **g))
    return {key: labels for key, labels in votes.items() if len({g["label"] for g in labels}) > 1}


def adjudicate(row: dict[str, Any], body: Annotation, actor: dict[str, Any]) -> dict[str, Any]:
    """分歧由第三位独立主体按输入裁决，保留原两票，不用被测输出确认金标。"""
    require(
        row["status"] == "REVIEWING" and len(row["annotations"]) >= 2,
        "Adjudication needs two blind annotations",
    )
    require(
        body.reviewer_id not in {*row["annotations"], row["intake"]["author_id"]},
        "Adjudicator must be independent of author/reviewers",
    )
    require(body.input_hash == row["input_hash"], "Adjudication input differs")
    disputes = disagreements(row)
    require(
        len(body.labels) == len(disputes) and {g.turn_id for g in body.labels} == set(disputes),
        "Resolve exactly disputed inputs",
    )
    value = copy.deepcopy(row)
    for g in body.labels:
        require(
            g.label in LABELS and g.case_id == disputes[g.turn_id][0]["case_id"],
            "Invalid adjudicated label/case",
        )
        value["resolutions"][g.turn_id] = dict(
            **g.model_dump(mode="json"),
            reviewer_id=body.reviewer_id,
            source_receipt=body.source_receipt,
            process=body.process.model_dump(mode="json"),
            submitted_by=actor,
        )
    return value


def freeze(row: dict[str, Any]) -> dict[str, Any]:
    """锁定输入、split、每条金标与来源收据；之后只读，重新开发须新独立holdout。"""
    require(
        row["status"] == "REVIEWING" and len(row["annotations"]) >= 2,
        "DATA_NOT_AVAILABLE: two independent blind annotations required",
    )
    require(set(disagreements(row)) <= set(row["resolutions"]), "Unresolved blind disagreements")
    first = next(iter(row["annotations"].values()))
    gold = {g["turn_id"]: g for g in first["labels"]}
    gold.update(
        {
            k: {n: v[n] for n in ("case_id", "turn_id", "label", "basis")}
            for k, v in row["resolutions"].items()
        }
    )
    value = copy.deepcopy(row)
    payload = {
        k: value[k]
        for k in (
            "dataset_id",
            "intake",
            "input_hash",
            "annotations",
            "resolutions",
            "historical_hash",
        )
    }
    payload["gold"] = gold
    payload["freeze_hash"] = digest(payload)
    value.update(status="FROZEN", frozen=payload)
    return value


def verify(snapshot: dict[str, Any]) -> None:
    require(
        snapshot.get("freeze_hash")
        == digest({k: v for k, v in snapshot.items() if k != "freeze_hash"}),
        "Frozen dataset hash changed",
    )
    intake = Intake.model_validate(snapshot["intake"])
    rebuilt = receive(intake, {}, [])
    require(
        rebuilt["dataset_id"] == snapshot["dataset_id"]
        and rebuilt["input_hash"] == snapshot["input_hash"],
        "Frozen source identity differs",
    )
    rebuilt["historical_hash"] = snapshot["historical_hash"]
    for raw in snapshot["annotations"].values():
        rebuilt = annotate(
            rebuilt,
            Annotation.model_validate(
                {k: v for k, v in raw.items() if k not in {"submitted_by", "submitted_at"}}
            ),
            raw["submitted_by"],
        )
    rebuilt["resolutions"] = copy.deepcopy(snapshot["resolutions"])
    rebuilt = freeze(rebuilt)
    require(rebuilt["frozen"]["gold"] == snapshot["gold"], "Frozen gold differs from blind votes")


def summary(row: dict[str, Any]) -> dict[str, Any]:
    """浏览只投影来源与计数，不泄露未锁定实验的holdout金标或原始导入附件。"""
    intake = row["intake"]
    splits = intake["splits"]
    return dict(
        dataset_id=row["dataset_id"],
        revision=row["revision"],
        status=row["status"],
        source_kind=intake["source_kind"],
        author_id=intake["author_id"],
        input_hash=row["input_hash"],
        cases=len(intake["cases"]),
        turns=sum(len(c["turns"]) for c in intake["cases"]),
        split_counts=dict(Counter(splits.values())),
        reviewers=list(row["annotations"]),
        disagreements=len(disagreements(row)),
        unresolved=len(set(disagreements(row)) - set(row["resolutions"])),
        freeze_hash=(row["frozen"] or {}).get("freeze_hash"),
        model_calls=0,
        independence=(
            "Declared independent source and blind process; AI synthesis is not customer evidence"
        ),
    )

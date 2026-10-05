"""把当前消息划成独立诉求片段，并标记主诉、补充或新话题。

片段偏移始终指向原文；槽位列表、更正句和同一诉求的补充不机械按标点拆开。
这些标签供归属候选过滤，不能当作已确定的业务意图。
"""

import hashlib
import re

from deephelp_app.application.text_entity import (
    demand_type,
)
from deephelp_app.domain.models.conversation import RequestEnvelope
from deephelp_app.domain.models.text import DemandType

from .policy import CORRECTION, NEW_TOPIC, SPLIT, SUPPLEMENT


def node_id(channel: str, message: str, start: int) -> str:
    return hashlib.sha256(f"{channel}:{message}:{start}".encode()).hexdigest()[:24]


def function_of(text: str, *, history: bool) -> DemandType:
    if NEW_TOPIC.search(text):
        return DemandType.NEW_TOPIC
    if CORRECTION.search(text) or re.match(r"(?:仅|只)?补充|刚才|刚刚|说错|更正|改为", text):
        return DemandType.SUPPLEMENT
    if SUPPLEMENT.search(text) and (history or demand_type(text, False) == DemandType.UNKNOWN):
        return DemandType.SUPPLEMENT
    if re.fullmatch(r"(?:订单(?:号)?[：:\s]*[A-Za-z0-9_-]+)[，,\s]*", text):
        return DemandType.SUPPLEMENT
    return demand_type(text, False)


def fragments(request: RequestEnvelope) -> list[tuple[int, int]]:
    """Split only separately stated complaints, never a slot list/correction sentence."""
    if CORRECTION.search(request.raw_text):
        return [(0, len(request.raw_text))]
    pieces: list[tuple[int, int]] = []
    start = 0
    for match in SPLIT.finditer(request.raw_text):
        if match.start() > start:
            pieces.append((start, match.start()))
        start = match.end()
    if start < len(request.raw_text):
        pieces.append((start, len(request.raw_text)))
    complaints = [
        p
        for p in pieces
        if re.search(
            r"不能|无法|不可|用不了|用不上|不让|没|未|查|哪个|什么|为什么|不行",
            request.raw_text[p[0] : p[1]],
        )
        and re.search(r"订单|优惠|券|活动|收银台|满减|促销|免息", request.raw_text[p[0] : p[1]])
        and (
            re.search(r"优惠|券|活动|收银台|满减|促销|免息", request.raw_text[p[0] : p[1]])
            or re.search(r"不能|没有|没|未|不行|不对", request.raw_text[p[0] : p[1]])
        )
    ]
    # Repeating a complaint as background is one event; different objects or
    # different business concerns remain separately stated complaints.
    groups: list[tuple[frozenset[str], set[str]]] = []
    for left, right in complaints:
        piece = request.raw_text[left:right]
        concerns = frozenset(
            name
            for name, pattern in (
                ("coupon", r"券"),
                ("discount", r"优惠(?!券)|满减|折扣"),
                ("activity", r"活动|促销"),
            )
            if re.search(pattern, piece)
        )
        objects = set(
            re.findall(r"(?:订单(?:号)?|券(?:号)?)[：:\s]*([A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)", piece)
        )
        merged = False
        for known_concerns, known_objects in groups:
            if (
                concerns
                and concerns == known_concerns
                and (not objects or not known_objects or objects == known_objects)
            ):
                known_objects.update(objects)
                merged = True
                break
        if not merged:
            groups.append((concerns, objects))
    return complaints if len(groups) >= 2 else [(0, len(request.raw_text))]

"""Event membership components; never classify main intent or execute SOP tools."""

from deephelp_app.domain.models.entities import EntityName
from deephelp_app.domain.models.events import (
    EventMessage,
)


class ConstrainedUnionFind:
    """Check entire components on every union, including indirect ambiguous bridges."""

    def __init__(self, nodes: tuple[EventMessage, ...]) -> None:
        self.parents = {n.node_id: n.node_id for n in nodes}
        self.orders = {
            n.node_id: {e.value for e in n.entities if e.name == EntityName.ORDER_ID} for n in nodes
        }
        self.anchors = {n.node_id: {n.question_id} if n.question_id else set() for n in nodes}

    def find(self, node: str) -> str:
        while self.parents[node] != node:
            self.parents[node] = self.parents[self.parents[node]]
            node = self.parents[node]
        return node

    def union(self, left: str, right: str) -> bool:
        a, b = self.find(left), self.find(right)
        if a == b:
            return True
        orders, anchors = self.orders[a] | self.orders[b], self.anchors[a] | self.anchors[b]
        if len(orders) > 1 or len(anchors) > 1:
            return False
        self.parents[b] = a
        self.orders[a], self.anchors[a] = orders, anchors
        return True

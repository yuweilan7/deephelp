"""合并归属图的连通分量，拒绝通过间接桥接混入两个订单或问题锚点。

union每次检查整个分量的对象集合，不能只看新连的两个节点。
更正改变编号并不证明两个旧对象相等，调用者对更正只记录归属边，不做等价合并。
"""

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

"""Declarative, acyclic SOPs and immutable content-addressed registry snapshots."""

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from deephelp_app.domain.models import (
    DTO,
    EntityName,
    Identifier,
    IntentCode,
    Money,
    SOPStatus,
    ToolName,
    VersionManifest,
)
from deephelp_app.domain.registry import complaint_registry
from deephelp_app.mcp_protocol import canonical, registered_tools, tool_schema
from deephelp_app.sop_config import SOPLimits

# A configuration can select within these grants, never extend them.
TOOL_GRANTS = {
    IntentCode.DISCOUNT_MISSING: (ToolName.GET_ORDER_BENEFITS,),
    IntentCode.COUPON_UNUSABLE: (ToolName.CHECK_COUPON, ToolName.GET_ORDER_BENEFITS),
    IntentCode.ORDER_ACTIVITY_QUERY: (ToolName.GET_ORDER_BENEFITS,),
}
FACT_TYPES = {
    ToolName.GET_ORDER_BENEFITS: {
        "order_id": "text",
        "paid": "money",
        "discount": "money",
        "discount_status": "text",
        "activity_ids": "text",
        "activity_labels": "text",
    },
    ToolName.CHECK_COUPON: {
        "order_id": "text",
        "coupon_id": "text",
        "coupon_status": "text",
        "minimum_spend": "money",
        "usable": "flag",
    },
}


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def schema_signature() -> str:
    return digest([t.model_dump(mode="json") for t in registered_tools()])


class FlowCondition(DTO):
    fact: Identifier  # lookup-node.fact-name; no expression interpreter
    op: Literal["eq", "empty", "nonempty", "fact_eq", "money_lt", "money_gte"]
    value: str | bool | None = None
    other_fact: Identifier | None = None

    @model_validator(mode="after")
    def operands(self) -> FlowCondition:
        if (self.op == "eq") != (self.value is not None) or (
            self.op in {"fact_eq", "money_lt", "money_gte"}
        ) != (self.other_fact is not None):
            raise ValueError("Invalid finite condition operands")
        return self

    def matches(self, facts: dict[str, str | bool | Money]) -> bool:
        left = facts[self.fact]
        right = facts[self.other_fact] if self.other_fact else self.value
        if self.op == "eq" or self.op == "fact_eq":
            return type(left) is type(right) and left == right
        if self.op == "empty":
            return left == ""
        if self.op == "nonempty":
            return isinstance(left, str) and bool(left)
        if not isinstance(left, Money) or not isinstance(right, Money):
            raise ValueError("Money comparison requires verified money")
        if left.currency != right.currency:
            raise ValueError("Currency mismatch")
        return left.amount < right.amount if self.op == "money_lt" else left.amount >= right.amount


class FlowRoute(DTO):
    condition: FlowCondition
    target: Identifier


class FlowNode(DTO):
    node_id: Identifier
    kind: Literal["lookup", "branch", "end"]
    tool: ToolName | None = None
    success: Identifier | None = None
    failure: Identifier | None = None
    routes: tuple[FlowRoute, ...] = Field(default=(), max_length=8)
    default: Identifier | None = None
    status: (
        Literal[
            SOPStatus.RESOLVED, SOPStatus.HANDED_OFF, SOPStatus.FAILED, SOPStatus.NEEDS_APPROVAL
        ]
        | None
    ) = None
    conclusion: str | None = Field(default=None, min_length=1, max_length=1000)
    proposal: Literal["simulate_discount_adjustment"] | None = None

    @model_validator(mode="after")
    def shape(self) -> FlowNode:
        valid: object
        if self.kind == "lookup":
            valid = (
                self.tool
                and self.success
                and self.failure
                and not (
                    self.routes or self.default or self.status or self.conclusion or self.proposal
                )
            )
        elif self.kind == "branch":
            valid = (
                self.routes
                and self.default
                and not (
                    self.tool
                    or self.success
                    or self.failure
                    or self.status
                    or self.conclusion
                    or self.proposal
                )
            )
        else:
            valid = (
                self.status
                and self.conclusion
                and not (self.tool or self.success or self.failure or self.routes or self.default)
                and ((self.status == SOPStatus.NEEDS_APPROVAL) == (self.proposal is not None))
            )
        if not valid:
            raise ValueError("Invalid node shape")
        return self

    def targets(self) -> tuple[str, ...]:
        if self.kind == "lookup":
            assert self.success and self.failure
            return self.success, self.failure
        if self.kind == "branch":
            assert self.default
            return (*[r.target for r in self.routes], self.default)
        return ()


class GovernedSOP(DTO):
    schema_version: Literal["m14-flow-v1"] = "m14-flow-v1"
    sop_id: Identifier
    version: Identifier
    intent_code: IntentCode
    required_slots: tuple[EntityName, ...]
    allowed_tools: tuple[ToolName, ...] = Field(min_length=1, max_length=2)
    tool_version: Literal["m06-readonly-v1"] = "m06-readonly-v1"
    evidence_version: Literal["business-fixtures-v1"] = "business-fixtures-v1"
    prompt_version: Literal["sop-governed-v1"] = "sop-governed-v1"
    entry: Identifier
    nodes: tuple[FlowNode, ...] = Field(min_length=2, max_length=32)
    limits: SOPLimits = Field(default_factory=SOPLimits)

    @model_validator(mode="after")
    def topology(self) -> GovernedSOP:
        item = complaint_registry().get(self.intent_code)
        if (
            not item.is_actionable
            or self.sop_id != item.sop_id
            or self.required_slots != item.required_slots
            or len(set(self.allowed_tools)) != len(self.allowed_tools)
            or not set(self.allowed_tools) <= set(TOOL_GRANTS.get(self.intent_code, ()))
        ):
            raise ValueError("Flow exceeds fixed intent grants or slot contract")
        index = {n.node_id: n for n in self.nodes}
        if len(index) != len(self.nodes) or self.entry not in index:
            raise ValueError("Duplicate node ID or missing entry")
        for n in self.nodes:
            if not set(n.targets()) <= set(index):
                raise ValueError("Unknown node reference")
            if n.tool:
                if n.tool not in self.allowed_tools:
                    raise ValueError("Unpublished tool")
                if not set(tool_schema(n.tool)["required"]) <= {
                    s.value for s in self.required_slots
                }:
                    raise ValueError("Tool slots incompatible with flow")
                assert n.failure
                if index[n.failure].kind != "end" or index[n.failure].status not in {
                    SOPStatus.HANDED_OFF,
                    SOPStatus.FAILED,
                }:
                    raise ValueError("Failed lookup must stop at a non-success end")
            if n.proposal and self.intent_code != IntentCode.DISCOUNT_MISSING:
                raise ValueError("Proposal exceeds fixed action grant")
        visited: set[str] = set()
        stack: set[str] = set()
        depths: dict[str, int] = {}

        def visit(key: str) -> int:
            if key in depths:
                return depths[key]
            if key in stack:
                raise ValueError("Cycles are unsupported; all flows must terminate")
            stack.add(key)
            visited.add(key)
            depth = 1 + max((visit(t) for t in index[key].targets()), default=0)
            stack.remove(key)
            depths[key] = depth
            return depth

        if visit(self.entry) > self.limits.max_steps or visited != set(index):
            raise ValueError("Unreachable node or insufficient step budget")
        lookups = {n.node_id: n for n in self.nodes if n.tool}
        if {n.tool for n in lookups.values()} != set(self.allowed_tools):
            raise ValueError("Unused tool grant")
        if len(lookups) > self.limits.max_tool_calls:
            raise ValueError("Insufficient tool budget")

        # Check every path, including failed lookups: conditions may only read facts
        # guaranteed to exist before that node. At most 32 nodes, no loops.
        checked: set[tuple[str, frozenset[str]]] = set()

        def check_path(key: str, available: frozenset[str]) -> None:
            if (key, available) in checked:
                return
            checked.add((key, available))
            node = index[key]
            if node.kind == "lookup":
                assert node.success and node.failure
                check_path(node.success, available | {key})
                check_path(node.failure, available)
            elif node.kind == "branch":
                signatures: set[str] = set()
                for route in node.routes:
                    c = route.condition
                    signature = c.model_dump_json()
                    if signature in signatures:
                        raise ValueError("Duplicate branch condition")
                    signatures.add(signature)
                    kinds = []
                    for ref in (c.fact, c.other_fact):
                        if ref is None:
                            continue
                        source, sep, fact = ref.partition(".")
                        lookup = lookups.get(source)
                        if (
                            not sep
                            or source not in available
                            or lookup is None
                            or lookup.tool is None
                        ):
                            raise ValueError("Fact is unavailable on a branch path")
                        kind = FACT_TYPES[lookup.tool].get(fact)
                        if kind is None:
                            raise ValueError("Unknown fact reference")
                        kinds.append(kind)
                    if (
                        (c.op in {"money_lt", "money_gte"} and kinds != ["money", "money"])
                        or (c.op == "fact_eq" and kinds[0] != kinds[1])
                        or (c.op in {"empty", "nonempty"} and kinds != ["text"])
                        or (
                            c.op == "eq"
                            and kinds != ["flag" if isinstance(c.value, bool) else "text"]
                        )
                    ):
                        raise ValueError("Incompatible condition fact types")
                for target in node.targets():
                    check_path(target, available)
            elif node.status in {SOPStatus.RESOLVED, SOPStatus.NEEDS_APPROVAL} and not available:
                raise ValueError("Successful/proposed end requires lookup evidence")

        check_path(self.entry, frozenset())
        return self


class SOPRegistry(DTO):
    format: Literal["m14-registry-v1"] = "m14-registry-v1"
    registry_version: Identifier
    tool_schema_hash: Identifier
    prompt_version: Literal["sop-governed-v1"] = "sop-governed-v1"
    prompt: str = Field(min_length=1, max_length=16000)
    definitions: tuple[GovernedSOP, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def bindings(self) -> SOPRegistry:
        codes = [d.intent_code for d in self.definitions]
        if (
            set(codes) != set(TOOL_GRANTS)
            or len(set(codes)) != len(codes)
            or self.tool_schema_hash != schema_signature()
            or any(d.prompt_version != self.prompt_version for d in self.definitions)
            or len({d.version for d in self.definitions}) != len(self.definitions)
        ):
            raise ValueError("Registry mapping, prompt or tool schema signature mismatch")
        return self

    @property
    def snapshot_hash(self) -> str:
        return digest(self.model_dump(mode="json"))

    def definition(self, code: IntentCode) -> GovernedSOP:
        return next(d for d in self.definitions if d.intent_code == code)

    def pin(self, versions: VersionManifest, code: IntentCode) -> VersionManifest:
        return versions.model_copy(
            update={
                "sop": self.definition(code).version,
                "sop_registry": self.registry_version,
                "sop_snapshot": self.snapshot_hash,
                "sop_prompt": self.prompt_version,
                "tool_schema": self.tool_schema_hash,
            }
        )


def bundled_registry() -> SOPRegistry:
    return SOPRegistry.model_validate_json(
        files("deephelp_app").joinpath("sop_data/registry-v1.json").read_text(encoding="utf-8")
    )


class RegistryStore:
    """One local writer, atomic pointer and retained snapshots; no live-process mutation."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.pointer = directory / "active.json"

    def _snapshot_path(self, key: str) -> Path:
        if (
            not key.startswith("sha256:")
            or len(key) != 71
            or any(c not in "0123456789abcdef" for c in key[7:])
        ):
            raise ValueError("Invalid snapshot hash")
        return self.directory / (key[7:] + ".json")

    def load(self, key: str) -> SOPRegistry:
        snapshot = SOPRegistry.model_validate_json(
            self._snapshot_path(key).read_text(encoding="utf-8")
        )
        if snapshot.snapshot_hash != key:
            raise ValueError("Snapshot content hash mismatch")
        return snapshot

    def history(self) -> tuple[SOPRegistry, ...]:
        if not self.pointer.exists():
            return ()
        pointer = json.loads(self.pointer.read_text(encoding="utf-8"))
        if (
            set(pointer) != {"format", "active", "previous", "snapshots"}
            or pointer["format"] != "m14-pointer-v1"
        ):
            raise ValueError("Invalid SOP pointer")
        keys = pointer["snapshots"]
        if not isinstance(keys, list) or not 1 <= len(keys) <= 64 or len(set(keys)) != len(keys):
            raise ValueError("Invalid retained snapshot index")
        if pointer["active"] not in keys or (
            pointer["previous"] is not None and pointer["previous"] not in keys
        ):
            raise ValueError("Pointer references unretained snapshot")
        return tuple(
            self.load(k) for k in [pointer["active"], *[k for k in keys if k != pointer["active"]]]
        )

    @contextmanager
    def _writer(self) -> Iterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True)
        lock_path = self.directory / "publish.lock"
        lock = lock_path.open("x", encoding="utf-8")
        try:
            yield
        finally:
            lock.close()
            lock_path.unlink(missing_ok=True)

    def publish(self, registry: SOPRegistry) -> str:
        registry = SOPRegistry.model_validate(registry.model_dump())
        with self._writer():
            return self._publish(registry)

    def _publish(self, registry: SOPRegistry) -> str:
        history = self.history()
        for old in history:
            if old.prompt_version == registry.prompt_version and old.prompt != registry.prompt:
                raise ValueError("Prompt version cannot be reused for different content")
            if (
                old.registry_version == registry.registry_version
                and old.snapshot_hash != registry.snapshot_hash
            ):
                raise ValueError("Registry version cannot be reused for different content")
            for definition in registry.definitions:
                prior = old.definition(definition.intent_code)
                if prior.version == definition.version and prior != definition:
                    raise ValueError("SOP version cannot be reused for different content")
        key = registry.snapshot_hash
        if history and history[0].snapshot_hash == key:
            return key
        if len(history) >= 64 and key not in {s.snapshot_hash for s in history}:
            raise ValueError("Retained snapshot limit reached; retain old question versions")
        target = self._snapshot_path(key)
        if target.exists():
            self.load(key)
        else:
            with target.open("x", encoding="utf-8") as out:
                out.write(registry.model_dump_json(indent=2))
        self.load(key)
        pointer = {
            "format": "m14-pointer-v1",
            "active": key,
            "previous": history[0].snapshot_hash if history else None,
            "snapshots": sorted({key, *[s.snapshot_hash for s in history]}),
        }
        temporary = self.directory / "active.tmp"
        temporary.write_bytes(canonical(pointer))
        temporary.replace(self.pointer)
        return key

    def rollback(self) -> str:
        with self._writer():
            pointer = json.loads(self.pointer.read_text(encoding="utf-8"))
            if not pointer.get("previous"):
                raise ValueError("No previous snapshot")
            return self._publish(self.load(pointer["previous"]))

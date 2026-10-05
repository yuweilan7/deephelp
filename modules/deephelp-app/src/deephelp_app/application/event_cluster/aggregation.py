"""Event membership aggregation; never classify main intent or execute SOP tools."""

import asyncio

from deephelp_app.adapters.cases import OPEN
from deephelp_app.application.text_entity import (
    TextEntityProcessor,
    TextPolicy,
    clean_text,
    demand_type,
    merge_entities,
    reconcile_conflicts,
)
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import (
    ErrorCode,
)
from deephelp_app.domain.models.conversation import Question, RequestEnvelope
from deephelp_app.domain.models.entities import Entity, EntityConflict, EntityName
from deephelp_app.domain.models.events import (
    ClusterEdge,
    ClusterJudgement,
    EventCandidate,
    EventClusterResult,
    EventContext,
    EventMessage,
)
from deephelp_app.domain.models.memory import MemoryWindow
from deephelp_app.domain.models.text import DemandType, TextEntityResult
from deephelp_app.domain.ports import CaseRepository, ClusterJudgePort

from .components import ConstrainedUnionFind
from .fragments import fragments, function_of, node_id
from .policy import CORRECTION, ClusterPolicy, EventSimilarityPort
from .window import MessageWindowAssembler


class EventAggregationService:
    def __init__(
        self,
        repository: CaseRepository,
        judge: ClusterJudgePort,
        *,
        similarity: EventSimilarityPort | None = None,
        text: TextEntityProcessor | None = None,
        policy: ClusterPolicy | None = None,
    ) -> None:
        self.repository, self.judge, self.similarity = repository, judge, similarity
        self.text = text or TextEntityProcessor()
        self.policy = policy or ClusterPolicy()
        self.assembler = MessageWindowAssembler(repository, self.policy)

    async def aggregate(
        self,
        request: RequestEnvelope,
        budget: ExecutionBudget,
        *,
        stateless_preview: bool = False,
        extracted: TextEntityResult | None = None,
        window: MemoryWindow | None = None,
        allow_unclassified: bool = False,
    ) -> EventClusterResult:
        async with asyncio.timeout(budget.remaining_seconds()):
            return await self._aggregate(
                request,
                budget,
                stateless_preview=stateless_preview,
                extracted=extracted,
                window=window,
                allow_unclassified=allow_unclassified,
            )

    async def _aggregate(
        self,
        request: RequestEnvelope,
        budget: ExecutionBudget,
        *,
        stateless_preview: bool,
        extracted: TextEntityResult | None = None,
        window: MemoryWindow | None = None,
        allow_unclassified: bool = False,
    ) -> EventClusterResult:
        diagnostics: list[str] = []
        window = (
            MemoryWindow() if stateless_preview else window or await self.assembler.load(request)
        )
        if any(
            q.identity != request.identity or q.session_id != request.session_id
            for q in window.active_questions
        ):
            raise AppError(ErrorCode.FORBIDDEN, "Event window scope mismatch")
        if stateless_preview:
            if request.question_hint:
                raise AppError(ErrorCode.INVALID_ARGUMENT, "Stateless preview cannot attach a hint")
            diagnostics.append("stateless_current_message_only")
        if window.trimmed:
            diagnostics.append("bounded_window_trimmed")
        eligible = {
            q.question_id: q
            for q in window.active_questions
            if q.status in OPEN
            and (not q.aggregation_pending or request.question_hint == q.question_id)
        }
        eligible_ids = set(eligible)
        historical: list[EventMessage] = []
        for row in window.history:
            if row.question_id not in eligible_ids or row.truncated:
                continue
            if row.occurred_at > request.occurred_at:
                diagnostics.append("future_history_excluded")
                continue
            # Use current confirmed IDs for constraints; historical text retains old values.
            historical.append(
                EventMessage(
                    node_id=node_id(row.channel, row.message_id, 0),
                    message_id=row.message_id,
                    channel=row.channel,
                    question_id=row.question_id,
                    cleaned_text=clean_text(row.text, TextPolicy()).cleaned_text,
                    entities=eligible[row.question_id].entities,
                    demand_type=demand_type(row.text, False),
                    end=len(row.text),
                )
            )
        visible = {m.question_id for m in historical}
        eligible = {qid: q for qid, q in eligible.items() if qid in visible}
        text = extracted or await self.text.process(request, budget, fields=())
        if text.message_id != request.message_id or text.clean.raw_text != request.raw_text:
            raise AppError(
                ErrorCode.INVALID_ARGUMENT, "Prepared extraction belongs to another message"
            )
        spans = fragments(request)
        current_nodes: list[EventMessage] = []
        for start, end in spans:
            entities = tuple(
                e.as_entity()
                for e in text.observations
                if start <= e.start < e.end <= end and e.disposition != "negated"
            )
            # Multiple observations for one field remain conflicts, not duplicate confirmed values.
            unique = {e.name: e for e in entities}
            current_nodes.append(
                EventMessage(
                    node_id=node_id(request.channel, request.message_id, start),
                    message_id=request.message_id,
                    channel=request.channel,
                    cleaned_text=clean_text(request.raw_text[start:end], TextPolicy()).cleaned_text,
                    entities=tuple(unique.values()),
                    demand_type=function_of(request.raw_text[start:end], history=bool(eligible)),
                    start=start,
                    end=end,
                )
            )
        nodes = tuple([*historical, *current_nodes])
        uf = ConstrainedUnionFind(nodes)
        edges: list[ClusterEdge] = []
        anchors: dict[str, str] = {}
        for message in historical:
            assert message.question_id is not None
            first = anchors.setdefault(message.question_id, message.node_id)
            accepted = uf.union(first, message.node_id)
            edges.append(
                ClusterEdge(
                    left=first,
                    right=message.node_id,
                    accepted=accepted,
                    reason="persisted_membership",
                )
            )
        candidates: list[EventCandidate] = []
        semantic: dict[str, EventCandidate] = {}
        current = current_nodes[0]
        if (
            self.similarity
            and eligible
            and len(spans) == 1
            and not stateless_preview
            and current.demand_type != DemandType.NEW_TOPIC
            and not request.question_hint
        ):
            try:
                hits = await self.similarity.candidates(
                    request.identity,
                    request.session_id,
                    text.clean.cleaned_text,
                    budget,
                    top_k=self.policy.top_k,
                )
                for hit in hits:
                    q = await self.repository.question(
                        request.identity, request.session_id, hit.question_id
                    )
                    if (
                        q
                        and q.question_id in eligible
                        and q.status in OPEN
                        and q.version == hit.version
                    ):
                        semantic[q.question_id] = hit
                    else:
                        candidates.append(
                            hit.model_copy(update={"excluded_reason": "stale_closed_or_scope"})
                        )
            except AppError as exc:
                if exc.code not in {ErrorCode.UPSTREAM_UNAVAILABLE, ErrorCode.TIMEOUT}:
                    raise
                diagnostics.append("similarity_unavailable_no_prelinks")
        correction = bool(CORRECTION.search(request.raw_text))
        current_values = {e.name: e.value for e in current.entities}
        for qid, q in eligible.items():
            values = {e.name: e.value for e in q.entities}
            matches = tuple(n for n, value in current_values.items() if values.get(n) == value)
            conflicts = [
                n for n, value in current_values.items() if n in values and value != values[n]
            ]
            semantic_hit = semantic.get(qid)
            reason = None
            if conflicts and not correction and request.question_hint != qid:
                reason = "entity_conflict"
            elif (
                current.demand_type == DemandType.SUPPLEMENT
                and not correction
                and not request.question_hint
                and q.active_intent
                and EntityName.COUPON_ID in current_values
                and EntityName.COUPON_ID not in q.active_intent.required_slots
            ):
                # Consume a confirmed binding; 500 never runs primary classification.
                reason = "slot_not_required_by_event"
            elif (
                semantic_hit
                and semantic_hit.raw_score is not None
                and semantic_hit.raw_score < self.policy.min_similarity
                and not matches
            ):
                reason = "low_similarity"
            candidates.append(
                EventCandidate(
                    question_id=qid,
                    version=q.version,
                    message_ids=q.member_message_ids,
                    text="\n".join(m.cleaned_text for m in historical if m.question_id == qid)[
                        : self.policy.summary_chars
                    ],
                    entities=q.entities,
                    entity_matches=matches,
                    raw_score=semantic_hit.raw_score if semantic_hit else None,
                    score_kind="cosine" if semantic_hit else None,
                    excluded_reason=reason,
                    active_intent=q.active_intent,
                )
            )
        available = sorted(
            (c for c in candidates if c.excluded_reason is None),
            key=lambda c: (
                -len(c.entity_matches),
                -(c.raw_score if c.raw_score is not None else -1),
                c.question_id,
            ),
        )
        selected = tuple(available[: self.policy.top_k])
        if len(available) > self.policy.top_k:
            diagnostics.append("candidate_topk_trimmed")
        judgement: ClusterJudgement | None = None
        target: Question | None = None
        disposition = "new"
        if len(spans) > 1:
            if request.question_hint:
                raise AppError(
                    ErrorCode.INVALID_ARGUMENT, "Multiple complaints cannot share one hint"
                )
            disposition = "clarify"
            diagnostics.append("multiple_independent_complaints_send_separately")
        elif request.question_hint:
            target = eligible.get(request.question_hint)
            if target is None:
                raise AppError(ErrorCode.FORBIDDEN, "Hint outside eligible event window")
            if (
                request.expected_question_version is not None
                and request.expected_question_version != target.version
            ):
                raise AppError(ErrorCode.VERSION_CONFLICT, "Question version changed")
            disposition = "attached"
        elif text.conflicts or text.unresolved_fields:
            disposition = "clarify"
            diagnostics.append("current_entity_ambiguity")
        elif current.demand_type == DemandType.NEW_TOPIC:
            diagnostics.append("explicit_new_topic")
        elif current.demand_type == DemandType.UNKNOWN:
            disposition = "new" if allow_unclassified else "clarify"
            diagnostics.append(
                "unclassified_current_message" if allow_unclassified else "unknown_message_function"
            )
        elif (
            len(selected) == 1
            and current.demand_type == DemandType.SUPPLEMENT
            and not correction
            and (bound := eligible[selected[0].question_id]).active_intent is not None
            and (provided := set(current_values) & {EntityName.ORDER_ID, EntityName.COUPON_ID})
            and provided <= set(bound.active_intent.required_slots)
            and provided
            & (
                set(bound.active_intent.required_slots)
                - {e.name for e in bound.entities if e.name not in bound.unresolved_fields}
            )
        ):
            # Explicit identifying slots and one compatible bound event are code facts.
            target, disposition = bound, "attached"
            diagnostics.append("unique_confirmed_missing_slot")
        elif selected:
            scoped_messages = tuple(
                m for m in historical if m.question_id in {c.question_id for c in selected}
            )
            try:
                async with asyncio.timeout(
                    min(self.policy.judge_timeout, budget.remaining_seconds())
                ):
                    judgement = await self.judge.judge(current, selected, scoped_messages, budget)
                target_id = self.validate_judgement(
                    judgement, current, selected, scoped_messages, correction
                )
                if target_id:
                    target = eligible[target_id]
                    disposition = "attached"
                elif (
                    judgement.decision == "new"
                    and judgement.confidence == "high"
                    and current.demand_type == DemandType.MAIN
                ):
                    disposition = "new"
                else:
                    disposition = "clarify"
            except AppError as exc:
                if exc.code != ErrorCode.MODEL_OUTPUT_INVALID:
                    raise
                diagnostics.append("judge_invalid_grounding")
                disposition = "clarify"
            except TimeoutError:
                raise AppError(ErrorCode.TIMEOUT, "Event judge timed out") from None
        elif current.demand_type == DemandType.SUPPLEMENT or text.unresolved_fields:
            disposition = "clarify"
            diagnostics.append("no_eligible_supplement_target")
        if target and request.question_hint:
            edges.append(
                ClusterEdge(
                    left=anchors[target.question_id],
                    right=current.node_id,
                    accepted=True,
                    reason="explicit_hint_membership_no_equivalence",
                )
            )
        elif target and not correction:
            accepted = uf.union(anchors[target.question_id], current.node_id)
            edges.append(
                ClusterEdge(
                    left=anchors[target.question_id],
                    right=current.node_id,
                    accepted=accepted,
                    reason="grounded_same_event" if accepted else "component_constraint",
                )
            )
            if not accepted:
                target, disposition = None, "clarify"
        elif target:
            # Correction changes an entity. It is never an A=B union proof.
            edges.append(
                ClusterEdge(
                    left=anchors[target.question_id],
                    right=current.node_id,
                    accepted=True,
                    reason="correction_membership_no_equivalence",
                )
            )
        observations = text.observations
        if correction:
            observations = [
                o.model_copy(update={"disposition": "correction"})
                if o.disposition != "negated"
                else o
                for o in observations
            ]
        merged_entities, merged_conflicts, unresolved = merge_entities(
            observations, target.entities if target else (), ()
        )
        prior_values = {e.name: e.value for e in target.entities} if target else {}
        confirmed_names = {
            o.name
            for o in observations
            if o.disposition != "negated"
            and (
                o.disposition == "correction"
                or o.name not in prior_values
                or prior_values[o.name] == o.value
            )
        }
        unresolved = sorted(
            set(unresolved)
            | set(text.unresolved_fields)
            | (set(target.unresolved_fields) - confirmed_names if target else set()),
            key=lambda n: n.value,
        )
        contexts: list[EventContext] = []
        for qid, q in eligible.items():
            if target and qid == target.question_id:
                continue
            ms = [m for m in historical if m.question_id == qid]
            contexts.append(
                self.context(qid, qid, ms, q.entities, q.conflicts, q.unresolved_fields)
            )
        current_context: EventContext | None = None
        if len(spans) == 1:
            ms = [
                m for m in historical if target and m.question_id == target.question_id
            ] + current_nodes
            current_context = self.context(
                target.question_id if target else current.node_id,
                target.question_id if target else None,
                ms,
                tuple(merged_entities),
                (
                    *reconcile_conflicts(
                        target.conflicts, merged_entities, confirmed_names, unresolved
                    ),
                    *merged_conflicts,
                )
                if target
                else tuple(merged_conflicts),
                tuple(unresolved),
            )
            contexts.append(current_context)
        else:
            for n in current_nodes:
                contexts.append(self.context(n.node_id, None, [n], n.entities, (), ()))
        graph = "\n".join(
            [
                *(
                    f"event {c.event_id}: messages={','.join(c.message_ids)}; {c.summary}"
                    for c in contexts
                ),
                *(
                    f"{e.left} -> {e.right}: {'accepted' if e.accepted else 'rejected'} {e.reason}"
                    for e in edges
                ),
            ]
        )
        return EventClusterResult.model_validate(
            dict(
                disposition=disposition,
                target_question_id=target.question_id if target else None,
                expected_version=target.version if target else None,
                current_event_context=current_context,
                events=tuple(contexts),
                window=nodes,
                candidates=tuple(candidates),
                judgement=judgement,
                edges=tuple(edges),
                diagnostics=tuple(diagnostics),
                graph_text=graph[:24000],
                generation=window.generation,
            )
        )

    def context(
        self,
        event_id: str,
        qid: str | None,
        messages: list[EventMessage],
        entities: tuple[Entity, ...],
        conflicts: tuple[EntityConflict, ...],
        unresolved: tuple[EntityName, ...],
    ) -> EventContext:
        # Cited extractive summary only; no model-written facts or business conclusions.
        lines: list[str] = []
        remaining = self.policy.summary_chars
        for message in reversed(messages):
            line = f"[{message.message_id}] {message.cleaned_text}"
            if len(line) + 1 > remaining:
                if not lines:
                    lines.append(line[: max(0, remaining - 1)] + "…")
                break
            lines.append(line)
            remaining -= len(line) + 1
        summary = "\n".join(reversed(lines))
        return EventContext(
            event_id=event_id,
            question_id=qid,
            message_ids=tuple(dict.fromkeys(m.message_id for m in messages)),
            node_ids=tuple(m.node_id for m in messages),
            summary=summary,
            entities=entities,
            conflicts=conflicts,
            unresolved_fields=unresolved,
        )

    @staticmethod
    def validate_judgement(
        judgement: ClusterJudgement,
        current: EventMessage,
        candidates: tuple[EventCandidate, ...],
        messages: tuple[EventMessage, ...],
        correction: bool,
    ) -> str | None:
        by_id = {c.question_id: c for c in candidates}
        if judgement.decision != "attach":
            if judgement.target_id is not None:
                raise AppError(
                    ErrorCode.MODEL_OUTPUT_INVALID, "Non-attach judgement supplied a target"
                )
            return None
        if judgement.target_id not in by_id or judgement.relation not in {
            "same_complaint",
            "supplement",
            "correction",
        }:
            raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Unknown event target or relation")
        if judgement.confidence != "high":
            return None
        if (judgement.relation == "correction") != correction:
            raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Ungrounded correction relation")
        target = by_id[judgement.target_id]
        sources = {m.message_id: m.cleaned_text for m in (*messages, current)}
        cited = set()
        for citation in judgement.citations:
            if (
                citation.message_id not in sources
                or citation.quote not in sources[citation.message_id]
            ):
                raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Ungrounded event citation")
            cited.add(citation.message_id)
        if current.message_id not in cited or not cited.intersection(target.message_ids):
            raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Event judgement needs both sources")
        return judgement.target_id

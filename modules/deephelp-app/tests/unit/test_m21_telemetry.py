"""Real OTel SDK context/timing/export with adversarial data and deterministic actions."""

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Link

from deephelp_app.adapters.mcp_protocol import sign_metadata, verify_metadata
from deephelp_app.adapters.telemetry import (
    META_KEY,
    BoundedProcessor,
    GuardedExporter,
    ObservedCursor,
    Telemetry,
    annotate,
    carrier,
    extracted,
    observation,
    observed,
    server_call,
    using,
)


@pytest.fixture
def telemetry():
    exporter = InMemorySpanExporter()
    runtime = Telemetry("test-service", exporter)
    with using(runtime):
        yield runtime, exporter
    runtime.shutdown()


def exported(pair):
    runtime, exporter = pair
    assert runtime.provider.force_flush(2000)
    return exporter.get_finished_spans()


async def test_sdk_task_parents_concurrent_duration_and_links(telemetry):
    with observation("run") as root:
        source = root.get_span_context()

        async def work(delay):
            with observation("child"):
                await asyncio.sleep(delay)

        await asyncio.gather(work(0.025), work(0.025))
    with observation("resume", parent=extracted({}), links=[Link(source)]):
        pass
    spans = exported(telemetry)
    children = [s for s in spans if s.name == "child"]
    assert len(children) == 2
    assert all(s.parent.span_id == source.span_id for s in children)
    assert all(s.context.trace_id == source.trace_id for s in children)
    resumed = next(s for s in spans if s.name == "resume")
    assert resumed.context.trace_id != source.trace_id
    assert resumed.links[0].context == source
    assert all(
        s.end_time >= s.start_time and s.attributes["deephelp.elapsed_ms"] >= 0 for s in spans
    )


async def test_cancellation_and_error_messages_cannot_escape(telemetry):
    @observed("cancelled")
    async def work():
        await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ValueError), observation("failed"):
        raise ValueError("password=SECRET hidden reasoning customer order private SQL")
    spans = exported(telemetry)
    assert {s.attributes["deephelp.status"] for s in spans} == {"cancelled", "failed"}
    serialized = " ".join(s.to_json() for s in spans)
    assert "SECRET" not in serialized and "hidden reasoning" not in serialized
    assert all(not s.events for s in spans)


async def test_signed_authority_is_independent_of_observation_and_server_parent(telemetry):
    secret = "s" * 64
    with observation("caller") as caller:
        metadata = sign_metadata(secret, {"identity": "AUTHORIZED"})
        assert verify_metadata(secret, metadata) == {"identity": "AUTHORIZED"}

        @server_call("mcp.business.call")
        async def handler(ctx, params):
            assert verify_metadata(secret, ctx.meta) == {"identity": "AUTHORIZED"}
            return SimpleNamespace(is_error=False)

        await handler(SimpleNamespace(meta=metadata), SimpleNamespace(name="get_order_benefits"))
        metadata[META_KEY] = {"traceparent": "password=SECRET"}
        assert verify_metadata(secret, metadata) == {"identity": "AUTHORIZED"}
        await handler(SimpleNamespace(meta=metadata), SimpleNamespace(name="SECRET"))
        source = caller.get_span_context()
    spans = exported(telemetry)
    servers = [s for s in spans if s.name == "mcp.business.call"]
    assert servers[0].parent.span_id == source.span_id
    assert servers[1].parent is None
    assert servers[1].attributes["rpc.method"] == "unregistered"
    assert "SECRET" not in " ".join(s.to_json() for s in spans)


@pytest.mark.parametrize(
    "parent", [None, "00-" + "0" * 32 + "-" + "1" * 16 + "-01", "ff-SECRET", "x" * 3000]
)
def test_invalid_w3c_context_creates_new_root(telemetry, parent):
    with observation("root", parent=extracted({"traceparent": parent})):
        assert carrier()["traceparent"].startswith("00-")
    assert exported(telemetry)[0].parent is None


async def test_sql_parameters_and_unapproved_attributes_are_never_exported(telemetry):
    class Cursor:
        async def execute(self, sql, parameters):
            return 3

    with observation("db"):
        annotate(**{"prompt": "SECRET", "password": "SECRET", "deephelp.input_length": 23})
        assert (
            await ObservedCursor(Cursor()).execute(
                "SELECT * FROM dh_m20_orders WHERE user_id=%s", ("SECRET",)
            )
            == 3
        )
    spans = exported(telemetry)
    query = next(s for s in spans if s.name == "mysql.execute")
    assert query.attributes["db.collection.name"] == "dh_m20_orders"
    assert query.attributes["db.operation.name"] == "SELECT"
    assert "SECRET" not in " ".join(s.to_json() for s in spans)


async def test_semantic_error_and_export_failure_preserve_result():
    class Failing(SpanExporter):
        def export(self, spans):
            raise RuntimeError("SECRET")

        def shutdown(self):
            pass

    runtime = Telemetry("fault", Failing())
    expected = SimpleNamespace(error=SimpleNamespace(code="UPSTREAM_UNAVAILABLE"))

    @observed("protocol")
    async def action():
        return expected

    with using(runtime):
        assert await action() is expected
    assert runtime.provider.force_flush(2000)
    assert runtime.exporter.failures == 1
    runtime.shutdown()


def test_full_export_queue_is_bounded_and_explicitly_counts_loss():
    gate, entered = threading.Event(), threading.Event()

    class Blocked(SpanExporter):
        def export(self, spans):
            entered.set()
            gate.wait(2)
            return SpanExportResult.SUCCESS

        def shutdown(self):
            gate.set()

    processor = BoundedProcessor(GuardedExporter(Blocked()), size=2)
    processor.on_end(SimpleNamespace())
    assert entered.wait(1)
    for _ in range(8):
        processor.on_end(SimpleNamespace())
    assert processor.queue.qsize() == 2
    assert processor.dropped == 6
    gate.set()
    assert processor.force_flush(2000)
    processor.shutdown()


def test_no_ambient_resource_data_or_incoming_sampling_drops(telemetry, monkeypatch):
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "password=SECRET,user.id=PRIVATE")
    with observation(
        "sampled", parent=extracted({"traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-00"})
    ):
        pass
    span = exported(telemetry)[0]
    assert span.context.trace_flags.sampled
    assert set(span.resource.attributes) == {
        "service.name",
        "service.version",
        "service.instance.id",
    }
    assert "SECRET" not in json.dumps(dict(span.resource.attributes))


async def test_metrics_have_distinct_outcomes_technical_failures_and_unknown_usage():
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from deephelp_app.adapters.telemetry import stage_event
    from deephelp_app.domain.errors import AppError
    from deephelp_app.domain.models import ErrorCode

    reader = InMemoryMetricReader()
    runtime = Telemetry("metrics", InMemorySpanExporter(), reader=reader)

    @observed("conversation.run")
    async def clarify():
        stage_event("700", "disabled")
        return SimpleNamespace(outcome="CLARIFY", replayed=False)

    with using(runtime):
        await clarify()
        with pytest.raises(AppError), observation("permission"):
            raise AppError(ErrorCode.FORBIDDEN, "SECRET")
        with pytest.raises(AppError), observation("model.attempt"):
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "SECRET")
    data = reader.get_metrics_data()
    metrics = {
        m.name: m for r in data.resource_metrics for scope in r.scope_metrics for m in scope.metrics
    }
    points = metrics["deephelp.operations"].data.data_points
    states = {p.attributes["operation"]: p.attributes["status"] for p in points}
    assert states == {
        "conversation.run": "completed",
        "permission": "rejected",
        "model.attempt": "failed",
    }
    assert metrics["deephelp.model.usage_unknown"].data.data_points[0].value == 1
    assert (
        metrics["deephelp.request.outcomes"].data.data_points[0].attributes["outcome"] == "CLARIFY"
    )
    assert all(set(p.attributes) <= {"operation", "status"} for p in points)
    assert all(p.value == 0 for p in metrics["deephelp.inflight"].data.data_points)
    runtime.shutdown()

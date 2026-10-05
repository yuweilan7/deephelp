import asyncio
import json
from uuid import uuid4

import pytest
from langgraph.graph import END, START, StateGraph
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from deephelp_app.adapters.observability.native_observation import (
    NativeTelemetry,
    graph_callbacks,
    model_io,
)
from deephelp_app.adapters.observability.telemetry import (
    annotate,
    carrier,
    extracted,
    observation,
    server_call,
    using,
)


@pytest.fixture
def native(tmp_path):
    path = tmp_path / "native.json"
    path.write_text(
        json.dumps(
            {
                "host": "http://127.0.0.1:9",
                "public_key": uuid4().hex,
                "secret_key": uuid4().hex,
                "redaction_key": "ab" * 32,
            }
        )
    )
    exporter = InMemorySpanExporter()
    runtime = NativeTelemetry("deephelp-test", str(path), exporter=exporter)
    with using(runtime):
        yield runtime, exporter
    runtime.shutdown()


async def test_native_parent_isolation_and_masked_graph_callbacks(native):
    runtime, exporter = native

    async def run():
        with observation("request") as root:
            upstream = carrier()
            with observation("mcp", parent=extracted(upstream)) as child:
                assert child.get_span_context().trace_id == root.get_span_context().trace_id
            callback = graph_callbacks()[0]
            graph = StateGraph(dict)

            async def wait(state):
                return {**state, "status": "finished"}

            graph.add_node("approval_wait", wait)
            graph.add_edge(START, "approval_wait").add_edge("approval_wait", END)
            await graph.compile().ainvoke(
                {"password": "DO_NOT_EXPORT", "user_id": "PRIVATE_SUBJECT"},
                {"callbacks": [callback]},
            )
            return root.get_span_context().trace_id, callback

    results = await asyncio.gather(run(), run())
    assert results[0][0] != results[1][0] and results[0][1] is not results[1][1]
    await asyncio.to_thread(runtime.client.flush)
    spans = exporter.get_finished_spans()
    assert {s.context.trace_id for s in spans} == {r[0] for r in results}
    assert any(s.name == "LangGraph" for s in spans)
    serialized = " ".join(s.to_json() for s in spans)
    assert "DO_NOT_EXPORT" not in serialized and "PRIVATE_SUBJECT" not in serialized


async def test_native_cancel_and_exception_do_not_capture_messages(native):
    runtime, exporter = native

    async def waiting():
        with observation("wait"):
            await asyncio.Event().wait()

    task = asyncio.create_task(waiting())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ValueError), observation("failed"):
        raise ValueError("password=DO_NOT_EXPORT hidden reasoning")
    await asyncio.to_thread(runtime.client.flush)
    spans = exporter.get_finished_spans()
    assert all(not s.events for s in spans)
    assert "DO_NOT_EXPORT" not in " ".join(s.to_json() for s in spans)
    assert {s.attributes["deephelp.status"] for s in spans} == {"cancelled", "failed"}


async def test_one_native_generation_per_actual_attempt_and_unknown_usage(native):
    runtime, exporter = native
    with observation("model.attempt"):
        annotate(
            **{
                "deephelp.model": "synthetic",
                "deephelp.attempt": 1,
                "deephelp.usage_known": True,
                "deephelp.input_tokens": 17,
                "deephelp.output_tokens": 9,
            }
        )
    with observation("model.attempt"):
        annotate(**{"deephelp.model": "synthetic", "deephelp.attempt": 2})
    await asyncio.to_thread(runtime.client.flush)
    rows = exporter.get_finished_spans()
    assert len(rows) == 2
    assert json.loads(rows[0].attributes["langfuse.observation.usage_details"]) == {
        "input": 17,
        "output": 9,
    }
    assert "langfuse.observation.usage_details" not in rows[1].attributes


async def test_native_mcp_parent_never_authorizes_and_invalid_parent_is_root(native):
    from types import SimpleNamespace

    runtime, exporter = native

    @server_call("mcp.business.call")
    async def handler(ctx, params):
        return SimpleNamespace(is_error=False)

    with observation("caller") as parent:
        await handler(
            SimpleNamespace(meta={"deephelp/observation": carrier()}),
            SimpleNamespace(name="get_order_benefits"),
        )
        await handler(
            SimpleNamespace(meta={"deephelp/observation": {"traceparent": "SECRET"}}),
            SimpleNamespace(name="SECRET"),
        )
        ctx = parent.get_span_context()
    assert not trace.get_current_span().get_span_context().is_valid
    await asyncio.to_thread(runtime.client.flush)
    servers = [s for s in exporter.get_finished_spans() if s.name == "mcp.business.call"]
    assert servers[0].parent.span_id == ctx.span_id
    assert servers[1].parent is None and servers[1].attributes["rpc.method"] == "unregistered"


async def test_generation_io_filters_reasoning_and_serialized_identifiers_without_mutation(native):
    runtime, exporter = native
    original = {"messages": [{"content": "订单0001234，password=DO_NOT_EXPORT"}]}
    output = {
        "choices": [
            {
                "message": {
                    "content": '{"order_id":"0001234","paid":"173.45"}',
                    "reasoning_content": "DO_NOT_EXPORT",
                }
            }
        ]
    }
    with observation("model.attempt"):
        model_io(input_value=original, output_value=output)
    await asyncio.to_thread(runtime.client.flush)
    row = exporter.get_finished_spans()[0]
    text = row.to_json()
    assert "DO_NOT_EXPORT" not in text and "0001234" not in text and "173.45" in text
    assert original["messages"][0]["content"].endswith("password=DO_NOT_EXPORT")
    assert "reasoning_content" in output["choices"][0]["message"]
    assert runtime.mask(data={"deephelp_safe_io": True, "content": "DO_NOT_EXPORT"}) == (
        "[structural observation; IO omitted]"
    )


async def test_observation_io_failure_cannot_change_actual_model_result(native, monkeypatch):
    with observation("model.attempt"):
        from deephelp_app.adapters.observability.native_observation import _active

        def failed(**kwargs):
            raise ValueError("DO_NOT_EXPORT")

        monkeypatch.setattr(_active.get(), "update", failed)
        model_io(input_value={"content": "safe"})
        # Invalid provider content still reaches the gateway's original parser.
        model_io(output_value={"choices": [{"message": None}, {"message": []}]})

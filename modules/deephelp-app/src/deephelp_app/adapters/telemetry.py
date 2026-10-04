"""Explicit, bounded OTel observations. Diagnostic context never grants authority.

Only allowlisted structural attributes leave the process; arguments, exception
messages, SQL parameters, prompts and hidden reasoning are never inspected.
"""

import asyncio
import functools
import os
import queue
import re
import threading
import time
from collections.abc import Callable, Coroutine, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, ParamSpec, TypeVar

from opentelemetry import context, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanLimits, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import Link, Span, SpanContext, SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from starlette.types import ASGIApp, Message, Receive, Scope, Send

P = ParamSpec("P")
T = TypeVar("T")
META_KEY = "deephelp/observation"
_selected: ContextVar[Telemetry | None] = ContextVar("deephelp_telemetry", default=None)
_default: Telemetry | None = None
PROPAGATOR = TraceContextTextMapPropagator()
ATTRIBUTES = frozenset(
    {
        "deephelp.run_id",
        "deephelp.question_id",
        "deephelp.operation_id",
        "deephelp.legacy_trace_id",
        "deephelp.request_id",
        "deephelp.status",
        "deephelp.stage",
        "deephelp.elapsed_ms",
        "deephelp.replayed",
        "deephelp.error_code",
        "deephelp.provider_request_id",
        "deephelp.model",
        "deephelp.attempt",
        "deephelp.retry",
        "deephelp.input_tokens",
        "deephelp.output_tokens",
        "deephelp.usage_known",
        "deephelp.cached",
        "deephelp.version",
        "deephelp.schema_hash",
        "deephelp.validation",
        "deephelp.checkpoint_id",
        "deephelp.parent_checkpoint_id",
        "deephelp.pending_writes",
        "deephelp.synthetic",
        "deephelp.outcome",
        "deephelp.input_length",
        "deephelp.code_entry",
        "deephelp.registry_version",
        "deephelp.sop_version",
        "deephelp.policy_version",
        "deephelp.embedding_signature",
        "deephelp.release_manifest",
        "deephelp.sop_node",
        "deephelp.sop_kind",
        "db.system.name",
        "db.operation.name",
        "db.collection.name",
        "rpc.method",
        "rpc.system",
        "http.request.method",
        "http.response.status_code",
    }
)


def current() -> Telemetry | None:
    return _selected.get() or _default


def safe_attributes(values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value[:160] if isinstance(value, str) else value
        for key, value in values.items()
        if key in ATTRIBUTES and isinstance(value, (str, bool, int, float))
    }


def annotate(**values: Any) -> None:
    trace.get_current_span().set_attributes(safe_attributes(values))


def stage_event(stage: str, status: str) -> None:
    span = trace.get_current_span()
    span.add_event("stage.state", attributes={"deephelp.stage": stage, "deephelp.status": status})
    if getattr(span, "name", None) == "stage." + stage:
        annotate(**{"deephelp.status": status})


def trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def carrier() -> dict[str, str]:
    result: dict[str, str] = {}
    PROPAGATOR.inject(result)
    return {k: v for k, v in result.items() if k == "traceparent"}


def extracted(value: Any) -> context.Context:
    # Only W3C v00, nonzero identifiers and valid flags; no tracestate/baggage.
    parent = value.get("traceparent") if isinstance(value, Mapping) else None
    if not isinstance(parent, str) or not re.fullmatch(
        r"00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}", parent
    ):
        return context.Context()
    return PROPAGATOR.extract({"traceparent": parent})


class GuardedExporter(SpanExporter):
    def __init__(self, exporter: SpanExporter) -> None:
        self.exporter, self.failures = exporter, 0

    def export(self, spans: Any) -> SpanExportResult:
        try:
            result = self.exporter.export(spans)
        except Exception:
            result = SpanExportResult.FAILURE
        if result != SpanExportResult.SUCCESS:
            self.failures += len(spans)
        return result

    def shutdown(self) -> None:
        self.exporter.shutdown()


class BoundedProcessor(SpanProcessor):
    """A standard SDK processor with exact queue-loss accounting and bounded shutdown."""

    def __init__(self, exporter: GuardedExporter, size: int = 512) -> None:
        self.exporter = exporter
        self.queue: queue.Queue[ReadableSpan] = queue.Queue(size)
        self.dropped = 0
        self.stopping = threading.Event()
        self.worker = threading.Thread(target=self._run, name="deephelp-otlp", daemon=True)
        self.worker.start()

    def on_start(self, span: Span, parent_context: context.Context | None = None) -> None:
        pass

    def on_end(self, span: ReadableSpan) -> None:
        if self.stopping.is_set():
            self.dropped += 1
            return
        try:
            self.queue.put_nowait(span)
        except queue.Full:
            self.dropped += 1

    def _run(self) -> None:
        while not self.stopping.is_set() or not self.queue.empty():
            try:
                batch = [self.queue.get(timeout=0.1)]
            except queue.Empty:
                continue
            while len(batch) < 64:
                try:
                    batch.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            try:
                self.exporter.export(batch)
            finally:
                for _ in batch:
                    self.queue.task_done()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        deadline = time.monotonic() + timeout_millis / 1000
        while self.queue.unfinished_tasks and time.monotonic() < deadline:
            self.stopping.wait(0.01)
        return self.queue.unfinished_tasks == 0

    def shutdown(self) -> None:
        self.stopping.set()
        self.worker.join(timeout=3)
        if self.worker.is_alive():
            # Remove and count only actually discarded records.
            while True:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
                self.queue.task_done()
                self.dropped += 1
        self.exporter.shutdown()


class Telemetry:
    def __init__(
        self,
        service: str,
        exporter: SpanExporter,
        *,
        endpoint: str | None = None,
        reader: MetricReader | None = None,
    ) -> None:
        resource = Resource(
            {
                "service.name": service,
                "service.version": "m21-v1",
                "service.instance.id": str(os.getpid()),
            }
        )
        self.service = service
        self.exporter = GuardedExporter(exporter)
        self.provider = TracerProvider(
            resource=resource,
            sampler=ALWAYS_ON,
            span_limits=SpanLimits(max_attributes=64, max_events=64, max_attribute_length=160),
        )
        self.processor = BoundedProcessor(self.exporter)
        self.provider.add_span_processor(self.processor)
        self.tracer = self.provider.get_tracer("deephelp.explicit", "m21-v1")
        self.setup_metrics(resource, endpoint, reader)

    def setup_metrics(
        self, resource: Resource, endpoint: str | None, reader: MetricReader | None
    ) -> None:
        readers = (
            [
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=endpoint + "/v1/metrics", timeout=2),
                    export_interval_millis=5000,
                    export_timeout_millis=3000,
                )
            ]
            if endpoint
            else [reader]
            if reader
            else []
        )
        self.metrics = MeterProvider(resource=resource, metric_readers=readers)
        meter = self.metrics.get_meter("deephelp.explicit", "m21-v1")
        self.operations = meter.create_counter("deephelp.operations", unit="{operation}")
        self.duration = meter.create_histogram(
            "deephelp.duration",
            unit="s",
            explicit_bucket_boundaries_advisory=(
                0.001,
                0.005,
                0.01,
                0.025,
                0.05,
                0.1,
                0.25,
                0.5,
                1,
                2.5,
                5,
                10,
                30,
                60,
            ),
        )
        self.inflight = meter.create_up_down_counter("deephelp.inflight", unit="{operation}")
        self.tokens = meter.create_counter("deephelp.model.tokens", unit="{token}")
        self.outcomes = meter.create_counter("deephelp.request.outcomes", unit="{request}")
        self.unknown_usage = meter.create_counter("deephelp.model.usage_unknown", unit="{attempt}")
        self.retries = meter.create_counter("deephelp.model.retries", unit="{attempt}")
        if hasattr(self, "exporter"):
            meter.create_observable_counter("deephelp.export.failed_spans", [self._failures])
            meter.create_observable_counter("deephelp.export.dropped_spans", [self._dropped])

    def _failures(self, options: Any) -> Any:
        from opentelemetry.metrics import Observation

        return [Observation(self.exporter.failures)]

    def _dropped(self, options: Any) -> Any:
        from opentelemetry.metrics import Observation

        return [Observation(self.processor.dropped)]

    def shutdown(self) -> None:
        self.provider.shutdown()
        self.metrics.shutdown()


@contextmanager
def using(runtime: Telemetry) -> Iterator[Telemetry]:
    token = _selected.set(runtime)
    try:
        yield runtime
    finally:
        _selected.reset(token)


@contextmanager
def environment(service: str) -> Iterator[Telemetry | None]:
    global _default
    runtime: Telemetry
    native_config = os.getenv("DEEPHELP_LANGFUSE_CONFIG")
    if native_config:
        from deephelp_app.adapters.native_observation import NativeTelemetry

        if isinstance(current(), NativeTelemetry):
            yield current()
            return
        runtime = NativeTelemetry(service, native_config)
        old, _default = _default, runtime
        try:
            yield runtime
        finally:
            _default = old
            runtime.shutdown()
        return
    endpoint = os.getenv("DEEPHELP_OTLP_ENDPOINT", "").rstrip("/")
    if not endpoint:
        yield None
        return
    from urllib.parse import urlsplit

    target = urlsplit(endpoint)
    if target.scheme not in {"http", "https"} or target.username or target.password:
        raise ValueError("OTLP requires an explicit HTTP(S) endpoint without credentials")
    runtime = Telemetry(
        service, OTLPSpanExporter(endpoint=endpoint + "/v1/traces", timeout=2), endpoint=endpoint
    )
    old, _default = _default, runtime
    try:
        yield runtime
    finally:
        _default = old
        runtime.shutdown()


@contextmanager
def observation(
    name: str,
    *,
    attributes: Mapping[str, Any] | None = None,
    parent: context.Context | None = None,
    links: list[Link] | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
) -> Iterator[Span]:
    runtime = current()
    tracer = runtime.tracer if runtime else trace.get_tracer("deephelp.disabled")
    started, status = time.perf_counter(), "completed"
    labels = {"operation": name}
    if runtime:
        runtime.inflight.add(1, labels)
    manager = (
        runtime.native_span(name, attributes or {}, parent, links or [])
        if runtime and hasattr(runtime, "native_span")
        else tracer.start_as_current_span(
            name,
            context=parent,
            links=links or [],
            kind=kind,
            attributes=safe_attributes(attributes or {}),
            record_exception=False,
            set_status_on_exception=False,
        )
    )
    with manager as span:
        try:
            yield span
        except BaseException as exc:
            code = str(getattr(exc, "code", type(exc).__name__))
            status = (
                "cancelled"
                if isinstance(exc, asyncio.CancelledError)
                else "interrupted"
                if type(exc).__name__ == "GraphInterrupt"
                else "rejected"
                if code in {"FORBIDDEN", "UNAUTHENTICATED", "INVALID_ARGUMENT", "VERSION_CONFLICT"}
                else "failed"
            )
            span.set_attribute("deephelp.error_code", str(getattr(exc, "code", type(exc).__name__)))
            if status == "failed":
                span.set_status(Status(StatusCode.ERROR))
            raise
        finally:
            # Returned semantic failures override transport completion.
            if span.is_recording():
                status = getattr(span, "attributes", {}).get("deephelp.status", status)
            elapsed = time.perf_counter() - started
            if status == "failed":
                span.set_status(Status(StatusCode.ERROR))
            span.set_attributes({"deephelp.status": status, "deephelp.elapsed_ms": elapsed * 1000})
            if runtime:
                runtime.inflight.add(-1, labels)
                runtime.operations.add(1, {**labels, "status": status})
                runtime.duration.record(elapsed, labels)
                if name == "model.attempt" and getattr(span, "attributes", {}).get(
                    "deephelp.retry", False
                ):
                    runtime.retries.add(1)
                if name == "model.attempt" and not getattr(span, "attributes", {}).get(
                    "deephelp.usage_known", False
                ):
                    runtime.unknown_usage.add(1)


def observed(
    name: str,
) -> Callable[[Callable[P, Coroutine[Any, Any, T]]], Callable[P, Coroutine[Any, Any, T]]]:
    def decorate(fn: Callable[P, Coroutine[Any, Any, T]]) -> Callable[P, Coroutine[Any, Any, T]]:
        @functools.wraps(fn)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            with observation(
                name, attributes={"deephelp.code_entry": fn.__module__ + "." + fn.__qualname__}
            ):
                result = await fn(*args, **kwargs)
                outcome = getattr(result, "outcome", None)
                if outcome is not None:
                    annotate(**{"deephelp.outcome": str(outcome)})
                    if runtime := current():
                        runtime.outcomes.add(
                            1,
                            {
                                "outcome": str(outcome),
                                "replayed": bool(getattr(result, "replayed", False)),
                            },
                        )
                error = getattr(result, "error", None)
                if error is not None or getattr(result, "is_error", False):
                    code = result_error_code(result)
                    annotate(
                        **{
                            "deephelp.status": "rejected"
                            if code
                            in {
                                "FORBIDDEN",
                                "UNAUTHENTICATED",
                                "INVALID_ARGUMENT",
                                "VERSION_CONFLICT",
                            }
                            else "failed",
                            "deephelp.error_code": code,
                        }
                    )
                return result

        return wrapped

    return decorate


def result_error_code(result: Any) -> str:
    from deephelp_app.domain.models import ErrorCode

    code = str(getattr(getattr(result, "error", None), "code", ""))
    content = getattr(result, "structured_content", None)
    if not code and isinstance(content, dict) and isinstance(content.get("error"), dict):
        code = str(content["error"].get("code", ""))
    return code if code in {item.value for item in ErrorCode} else "MODEL_OUTPUT_INVALID"


def checked(name: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    def decorate(fn: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(fn)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            with observation(name):
                result = fn(*args, **kwargs)
                annotate(**{"deephelp.validation": "passed"})
                return result

        return wrapped

    return decorate


def server_call(
    name: str,
) -> Callable[[Callable[P, Coroutine[Any, Any, T]]], Callable[P, Coroutine[Any, Any, T]]]:
    def decorate(fn: Callable[P, Coroutine[Any, Any, T]]) -> Callable[P, Coroutine[Any, Any, T]]:
        @functools.wraps(fn)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            ctx = args[0] if args else kwargs.get("ctx")
            meta = getattr(ctx, "meta", None) or {}
            params = args[1] if len(args) > 1 else kwargs.get("params")
            method = getattr(params, "name", name)
            if method not in {
                "get_order_benefits",
                "check_coupon",
                "retrieve_intents",
                "compare_intents",
                name,
            }:
                method = "unregistered"
            # Names are registered RPC names, never query text or user-provided IDs.
            with observation(
                name,
                parent=extracted(meta.get(META_KEY)),
                kind=SpanKind.SERVER,
                attributes={"rpc.system": "mcp", "rpc.method": method},
            ):
                result = await fn(*args, **kwargs)
                if getattr(result, "is_error", False):
                    code = result_error_code(result)
                    annotate(
                        **{
                            "deephelp.status": "rejected"
                            if code
                            in {
                                "FORBIDDEN",
                                "UNAUTHENTICATED",
                                "INVALID_ARGUMENT",
                                "VERSION_CONFLICT",
                            }
                            else "failed",
                            "deephelp.error_code": code,
                        }
                    )
                return result

        return wrapped

    return decorate


class ObservabilityMiddleware:
    def __init__(self, app: ASGIApp, *, accept_parent: bool = False) -> None:
        self.app = app
        self.accept_parent = accept_parent

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Fixed operation name avoids IDs, URLs, queries and metric cardinality.
        headers = dict(scope.get("headers", []))
        with observation(
            "http.request",
            parent=extracted(
                {"traceparent": headers.get(b"traceparent", b"").decode("ascii", errors="replace")}
            )
            if self.accept_parent
            else context.Context(),
            kind=SpanKind.SERVER,
            attributes={"http.request.method": scope["method"]},
        ):

            async def traced_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    code = message["status"]
                    annotate(
                        **{
                            "http.response.status_code": code,
                            "deephelp.status": "rejected"
                            if code in {401, 403, 404, 409, 422}
                            else "failed"
                            if code >= 500
                            else "completed",
                        }
                    )
                    if identifier := trace_id():
                        message = dict(message)
                        message["headers"] = [
                            (k, v)
                            for k, v in message.get("headers", [])
                            if k.lower() != b"x-otel-trace-id"
                        ] + [(b"x-otel-trace-id", identifier.encode())]
                await send(message)

            await self.app(scope, receive, traced_send)


class ObservedContext:
    def __init__(self, inner: Any, convert: Callable[[Any], Any]) -> None:
        self.inner, self.convert = inner, convert

    async def __aenter__(self) -> Any:
        return self.convert(await self.inner.__aenter__())

    async def __aexit__(self, *args: Any) -> Any:
        return await self.inner.__aexit__(*args)


class ObservedCursor:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor

    def __getattr__(self, name: str) -> Any:
        return getattr(self.cursor, name)

    async def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        match = re.search(r"\b(?:FROM|INTO|UPDATE|TABLE)\s+(dh_[a-z0-9_]+)", sql, re.I)
        command = sql.lstrip().split(" ", 1)[0].upper()
        with observation(
            "postgresql.execute",
            attributes={
                "db.system.name": "postgresql",
                "db.operation.name": command
                if command in {"SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"}
                else "OTHER",
                "db.collection.name": match.group(1) if match else "other",
            },
        ):
            return await self.cursor.execute(sql, *args, **kwargs)


class ObservedConnection:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def __getattr__(self, name: str) -> Any:
        return getattr(self.connection, name)

    def cursor(self, *args: Any, **kwargs: Any) -> ObservedContext:
        return ObservedContext(self.connection.cursor(*args, **kwargs), ObservedCursor)

    @observed("postgresql.commit")
    async def commit(self) -> Any:
        return await self.connection.commit()


class ObservedPool:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    def __getattr__(self, name: str) -> Any:
        return getattr(self.pool, name)

    def acquire(self) -> ObservedContext:
        return ObservedContext(self.pool.acquire(), ObservedConnection)


async def operation_link(pool: Any, operation_id: str, *, save: bool = False) -> list[Link]:
    """Optional diagnostic table; never alters business/checkpoint content or authority."""
    if current() is None:
        return []
    if hasattr(current(), "native_span"):
        from deephelp_app.adapters.native_observation import pause_links

        return [] if save else await pause_links(pool, operation_id)
    try:
        async with asyncio.timeout(0.5), pool.acquire() as conn, conn.cursor() as cursor:
            if save:
                ctx = trace.get_current_span().get_span_context()
                if ctx.is_valid:
                    await cursor.execute(
                        "INSERT INTO dh_m21_observation_links "
                        "(operation_id,trace_id,span_id) VALUES (%s,%s,%s) ON CONFLICT "
                        "(operation_id) DO NOTHING",
                        (operation_id, format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")),
                    )
                    await conn.commit()
                return []
            await cursor.execute(
                "SELECT trace_id,span_id FROM dh_m21_observation_links WHERE operation_id=%s",
                (operation_id,),
            )
            row = await cursor.fetchone()
            await conn.rollback()
            if row:
                ctx = SpanContext(int(row[0], 16), int(row[1], 16), is_remote=True)
                if ctx.is_valid:
                    return [Link(ctx)]
    except Exception:
        # Lost diagnostic links do not authorize/replay or block an operation.
        annotate(**{"deephelp.validation": "observation_link_unavailable"})
    return []

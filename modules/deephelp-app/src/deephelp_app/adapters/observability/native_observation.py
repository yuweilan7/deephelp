"""Native SDK observations at the existing explicit seams, with export-time guards."""

import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from langfuse import Langfuse
from langfuse.langchain import CallbackHandler
from langfuse.types import MaskOtelSpansParams, MaskOtelSpansResult, OtelSpanPatch
from opentelemetry import context, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Link, Span, SpanContext

from deephelp_app.adapters.observability.telemetry import Telemetry, current, safe_attributes
from deephelp_app.application.debug import sanitize
from deephelp_app.domain.errors import ConfigurationError

_active: ContextVar[Any] = ContextVar("deephelp_native_observation", default=None)


def configuration(path: str) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    address = urlsplit(value["host"])
    if (
        address.scheme not in {"http", "https"}
        or not address.hostname
        or address.username
        or address.password
        or address.query
        or address.fragment
        or address.path not in {"", "/"}
    ):
        raise ConfigurationError("Langfuse requires a private base URL with separate credentials")
    if not value.get("public_key") or not value.get("secret_key"):
        raise ConfigurationError("Langfuse server credentials are required")
    if len(bytes.fromhex(value["redaction_key"])) != 32:
        raise ConfigurationError("Langfuse requires a persistent 32-byte redaction key")
    return value


class NativeTelemetry(Telemetry):
    def __init__(self, service: str, path: str, *, exporter: Any = None) -> None:
        self.config = configuration(path)
        self.key = bytes.fromhex(self.config["redaction_key"])
        self.service = service
        self.io_marker = uuid4().hex
        self.provider = TracerProvider(resource=Resource({"service.name": service}))
        self.client = Langfuse(
            public_key=self.config["public_key"],
            secret_key=self.config["secret_key"],
            base_url=self.config["host"],
            tracer_provider=self.provider,
            timeout=3,
            flush_at=64,
            flush_interval=0.25,
            tracing_enabled=self.config.get("enabled", True),
            environment="deephelp-native",
            mask=self.mask,
            mask_otel_spans=self.mask_spans,
            span_exporter=exporter,
        )
        self.tracer = self.provider.get_tracer("deephelp.native")
        self.setup_metrics(Resource({"service.name": service}), None)

    def mask(self, *, data: Any, **kwargs: Any) -> Any:
        if data is None:
            return None
        # The official callback passes whole graph states here. Only gateway IO
        # has an explicit marker; graph arguments and state never leave the process.
        if isinstance(data, dict) and data.get("deephelp_safe_io") == self.io_marker:
            return sanitize({"content": data.get("content")}, self.key)["content"]
        return "[structural observation; IO omitted]"

    def mask_spans(self, *, params: MaskOtelSpansParams) -> MaskOtelSpansResult:
        patches = {}
        for identifier, span in params.spans.items():
            attrs = span.attributes
            metadata = {
                **safe_attributes(attrs),
                "service.name": self.service,
                "service.instance.id": str(os.getpid()),
            }
            for field in ("deephelp.predecessor_trace_id", "deephelp.predecessor_span_id"):
                if isinstance(attrs.get(field), str):
                    metadata[field] = attrs[field]
            # No callback metadata, SQL, credential, exception text, or graph state.
            deleted = tuple(
                key
                for key in attrs
                if key not in safe_attributes(attrs) and not key.startswith("langfuse.")
            ) + (
                "langfuse.observation.metadata",
                "langfuse.observation.status_message",
            )
            patches[identifier] = OtelSpanPatch(
                delete_attributes=deleted,
                set_attributes={"langfuse.observation.metadata": json.dumps(metadata)},
            )
        return MaskOtelSpansResult(span_patches=patches)

    @contextmanager
    def native_span(
        self,
        name: str,
        attributes: Mapping[str, Any],
        parent: context.Context | None,
        links: list[Link],
    ) -> Iterator[Span]:
        token = context.attach(parent) if parent is not None else None
        manager = self.client.start_as_current_observation(
            name=name,
            as_type="generation" if name == "model.attempt" else "span",
        )
        observation = manager.__enter__()
        active_token = _active.set(observation)
        span = trace.get_current_span()
        span.set_attributes(safe_attributes(attributes))
        if links:
            span.set_attributes(
                {
                    "deephelp.predecessor_trace_id": format(links[0].context.trace_id, "032x"),
                    "deephelp.predecessor_span_id": format(links[0].context.span_id, "016x"),
                }
            )
        try:
            yield span
        finally:
            attrs = getattr(span, "attributes", {}) or {}
            options: dict[str, Any] = {
                "level": "ERROR" if attrs.get("deephelp.status") == "failed" else "DEFAULT",
                "version": attrs.get("deephelp.version", "m28-native-v1"),
            }
            if name == "model.attempt":
                options["model"] = attrs.get("deephelp.model")
                if attrs.get("deephelp.usage_known"):
                    options["usage_details"] = {
                        "input": attrs["deephelp.input_tokens"],
                        "output": attrs["deephelp.output_tokens"],
                    }
            try:
                observation.update(**options)
            except Exception:
                pass
            finally:
                # Suppress automatic exception-event capture and always restore task context.
                try:
                    manager.__exit__(None, None, None)
                finally:
                    _active.reset(active_token)
                    if token is not None:
                        context.detach(token)

    def shutdown(self) -> None:
        try:
            self.client.shutdown()
        finally:
            self.metrics.shutdown()
            self.close_metrics()


def graph_callbacks() -> list[Any]:
    runtime = current()
    if not isinstance(runtime, NativeTelemetry) or not runtime.config.get("enabled", True):
        return []
    # Each invocation gets its own mutable handler. Never use last_trace_id to bind a run.
    span = trace.get_current_span().get_span_context()
    return (
        [
            CallbackHandler(
                public_key=runtime.config["public_key"],
                trace_context={
                    "trace_id": format(span.trace_id, "032x"),
                    "parent_span_id": format(span.span_id, "016x"),
                },
            )
        ]
        if span.is_valid
        else []
    )


def model_io(*, input_value: Any = None, output_value: Any = None) -> None:
    # 仅记录网关实际允许的脱敏IO，剔除隐藏推理；诊断失败不改变原解析/业务效果。
    runtime, active = current(), _active.get()
    if not isinstance(runtime, NativeTelemetry) or active is None:
        return

    def decode(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: decode(item)
                for key, item in value.items()
                if key not in {"reasoning_content", "reasoning", "thinking"}
            }
        if isinstance(value, (list, tuple)):
            return [decode(item) for item in value]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                return value
            return decode(parsed) if isinstance(parsed, (dict, list)) else value
        return value

    options = {}
    if input_value is not None:
        options["input"] = {"deephelp_safe_io": runtime.io_marker, "content": decode(input_value)}
    if isinstance(output_value, dict):
        choices = output_value.get("choices")
        final: Any
        if isinstance(choices, list):
            final = [
                {key: item["message"].get(key) for key in ("content", "tool_calls")}
                for item in choices
                if isinstance(item, dict) and isinstance(item.get("message"), dict)
            ]
        else:
            final = output_value.get("data", output_value.get("output"))
            if isinstance(final, list):
                final = [
                    {"index": item.get("index"), "dimensions": len(item["embedding"])}
                    if isinstance(item, dict) and isinstance(item.get("embedding"), list)
                    else item
                    for item in final
                ]
        if final is not None:
            options["output"] = {"deephelp_safe_io": runtime.io_marker, "content": decode(final)}
    if options:
        try:
            active.update(**options)
        except Exception:
            from deephelp_app.adapters.observability.telemetry import annotate

            annotate(**{"deephelp.validation": "observation_io_unavailable"})


async def register_run(
    pool: Any,
    run_id: str,
    *,
    kind: str = "run",
    operation_id: str | None = None,
    predecessor: list[Link] | None = None,
) -> None:
    import asyncio

    if not isinstance(current(), NativeTelemetry) or pool is None:
        return
    span = trace.get_current_span().get_span_context()
    if not span.is_valid:
        return
    link = predecessor[0].context if predecessor else None
    try:
        async with asyncio.timeout(0.5), pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO dh_m28_observation_runs "
                "(run_id,trace_id,span_id,kind,operation_id,"
                "predecessor_trace_id,predecessor_span_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    run_id,
                    format(span.trace_id, "032x"),
                    format(span.span_id, "016x"),
                    kind,
                    operation_id,
                    format(link.trace_id, "032x") if link else None,
                    format(link.span_id, "016x") if link else None,
                ),
            )
            await conn.commit()
    except Exception:
        from deephelp_app.adapters.observability.telemetry import annotate

        annotate(**{"deephelp.validation": "observation_link_unavailable"})


async def pause_links(pool: Any, operation_id: str) -> list[Link]:
    import asyncio

    try:
        async with asyncio.timeout(0.5), pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT trace_id,span_id FROM dh_m28_observation_runs "
                "WHERE operation_id=%s AND kind='pause' ORDER BY created_at LIMIT 1",
                (operation_id,),
            )
            row = await cursor.fetchone()
            await conn.rollback()
        if row:
            return [Link(SpanContext(int(row[0], 16), int(row[1], 16), is_remote=True))]
    except Exception:
        pass
    return []

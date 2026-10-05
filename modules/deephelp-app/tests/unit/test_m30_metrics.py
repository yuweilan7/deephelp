"""Actual pull protocol, cancellation, isolated registries and same-attempt denominators."""

import asyncio

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import generate_latest
from prometheus_client.parser import text_string_to_metric_families

from deephelp_app.adapters.observability.prometheus import MetricsEndpoint
from deephelp_app.adapters.observability.telemetry import Telemetry, annotate, observation, using


def values(endpoint):
    return {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in text_string_to_metric_families(generate_latest(endpoint.registry).decode())
        for sample in family.samples
    }


async def test_real_pull_preserves_units_buckets_and_usage_once_per_attempt():
    endpoint = MetricsEndpoint(0)
    runtime = Telemetry("test-direct", InMemorySpanExporter(), reader=endpoint.reader)
    try:
        with using(runtime):
            with observation("model.attempt"):
                annotate(**{"deephelp.usage_known": True})
                runtime.tokens.add(17, {"model": "fixed", "direction": "input"})
                runtime.tokens.add(9, {"model": "fixed", "direction": "output"})
            with pytest.raises(asyncio.CancelledError), observation("model.attempt"):
                raise asyncio.CancelledError()
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(f"http://127.0.0.1:{endpoint.server.server_port}/metrics")
        assert response.status_code == 200
        assert "test-direct" in response.text
        data = values(endpoint)

        def samples(name):
            return [(dict(labels), value) for (key, labels), value in data.items() if key == name]

        assert sum(v for _, v in samples("deephelp_operations_total")) == 2
        assert sum(v for _, v in samples("deephelp_model_usage_unknown_total")) == 1
        assert sum(v for _, v in samples("deephelp_model_tokens_total")) == 26
        assert sum(v for _, v in samples("deephelp_duration_seconds_count")) == 2
        assert sum(v for _, v in samples("deephelp_inflight")) == 0
        assert {label["status"] for label, _ in samples("deephelp_operations_total")} == {
            "completed",
            "cancelled",
        }
        buckets = samples("deephelp_duration_seconds_bucket")
        assert {label["le"] for label, _ in buckets} == {
            "0.001",
            "0.005",
            "0.01",
            "0.025",
            "0.05",
            "0.1",
            "0.25",
            "0.5",
            "1",
            "2.5",
            "5",
            "10",
            "30",
            "60",
            "+Inf",
        }
        # Scraping twice is cumulative, not another execution or usage event.
        assert values(endpoint) == data
    finally:
        runtime.shutdown()
        endpoint.close()


def test_registry_lifecycle_isolated_and_duplicate_metrics_export_rejected(monkeypatch):
    first, second = MetricsEndpoint(0), MetricsEndpoint(0)
    one = Telemetry("one", InMemorySpanExporter(), reader=first.reader)
    two = Telemetry("two", InMemorySpanExporter(), reader=second.reader)
    try:
        one.tokens.add(5)
        two.tokens.add(7)
        assert b" 5.0" in generate_latest(first.registry)
        assert b" 7.0" not in generate_latest(first.registry)
        monkeypatch.setenv("DEEPHELP_METRICS_PORT", "9464")
        # Fail before starting another endpoint or metric writer.
        with pytest.raises(ValueError, match="single exporter"):
            one.setup_metrics(None, first.reader)
    finally:
        one.shutdown()
        two.shutdown()
        first.close()
        second.close()


def test_retired_otlp_configuration_fails_before_dispatch(monkeypatch):
    from deephelp_app.adapters.observability.telemetry import environment

    monkeypatch.setenv("DEEPHELP_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    with pytest.raises(ValueError, match="retired"), environment("app"):
        pytest.fail("Retired configuration must not start a second exporter")

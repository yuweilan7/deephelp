"""One private pull endpoint per process; no Collector or duplicate export path."""

from opentelemetry.exporter.prometheus import PrometheusMetricReader
from prometheus_client import CollectorRegistry, ProcessCollector, start_http_server


class MetricsEndpoint:
    def __init__(self, port: int) -> None:
        self.registry = CollectorRegistry()
        ProcessCollector(registry=self.registry)
        self.reader = PrometheusMetricReader(
            registry=self.registry,
            scope_info_enabled=False,
            resource_attribute_filter=lambda name: name == "service.name",
        )
        try:
            self.server, self.thread = start_http_server(
                port, addr="127.0.0.1", registry=self.registry
            )
        except BaseException:
            self.reader.shutdown()
            raise

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

from __future__ import annotations

from nova.metrics.exporters import MetricsExporter
from nova.metrics.registry import get_metrics_registry
from nova.metrics.timers import Timer


class DummyExporter(MetricsExporter):
    def __init__(self) -> None:
        self.timings: list[tuple[str, float]] = []

    def increment(
        self,
        metric: str,
        value: int,
    ) -> None:
        pass

    def timing(
        self,
        metric: str,
        duration_ms: float,
    ) -> None:
        self.timings.append((metric, duration_ms))


class TestTimer:
    def setup_method(self) -> None:
        registry = get_metrics_registry()
        registry.clear()

    def test_timer_reports_metric(self) -> None:
        exporter = DummyExporter()

        registry = get_metrics_registry()
        registry.register_exporter(exporter)

        with Timer("query.time"):
            sum(range(1000))

        assert len(exporter.timings) == 1

        metric, duration = exporter.timings[0]

        assert metric == "query.time"
        assert duration >= 0
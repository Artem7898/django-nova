from __future__ import annotations

from nova.metrics.counters import Counter, increment
from nova.metrics.exporters import MetricsExporter
from nova.metrics.registry import get_metrics_registry


class DummyExporter(MetricsExporter):
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def increment(
        self,
        metric: str,
        value: int,
    ) -> None:
        self.calls.append((metric, value))

    def timing(
        self,
        metric: str,
        duration_ms: float,
    ) -> None:
        pass


class TestCounters:
    def setup_method(self) -> None:
        self.registry = get_metrics_registry()
        self.registry.clear()

    def test_increment(self) -> None:
        exporter = DummyExporter()

        self.registry.register_exporter(exporter)

        increment(Counter.CACHE_HIT)

        assert exporter.calls == [
            ("nova.cache.hit", 1),
        ]

    def test_custom_increment(self) -> None:
        exporter = DummyExporter()

        self.registry.register_exporter(exporter)

        increment(Counter.TASK_SUCCESS, 5)

        assert exporter.calls == [
            ("nova.task.success", 5),
        ]
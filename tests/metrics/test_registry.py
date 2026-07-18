from __future__ import annotations

from nova.metrics.exporters import MetricsExporter
from nova.metrics.registry import MetricsRegistry


class DummyExporter(MetricsExporter):
    def __init__(self) -> None:
        self.counters: list[tuple[str, int]] = []
        self.timings: list[tuple[str, float]] = []

    def increment(
        self,
        metric: str,
        value: int,
    ) -> None:
        self.counters.append((metric, value))

    def timing(
        self,
        metric: str,
        duration_ms: float,
    ) -> None:
        self.timings.append((metric, duration_ms))


class TestMetricsRegistry:
    def test_register_exporter(self) -> None:
        registry = MetricsRegistry()
        exporter = DummyExporter()

        registry.register_exporter(exporter)

        registry.increment("cache.hit")

        assert exporter.counters == [("cache.hit", 1)]

    def test_duplicate_registration_is_ignored(self) -> None:
        registry = MetricsRegistry()
        exporter = DummyExporter()

        registry.register_exporter(exporter)
        registry.register_exporter(exporter)

        registry.increment("metric")

        assert exporter.counters == [("metric", 1)]

    def test_unregister(self) -> None:
        registry = MetricsRegistry()
        exporter = DummyExporter()

        registry.register_exporter(exporter)
        registry.unregister_exporter(exporter)

        registry.increment("metric")

        assert exporter.counters == []

    def test_clear(self) -> None:
        registry = MetricsRegistry()

        registry.register_exporter(DummyExporter())
        registry.register_exporter(DummyExporter())

        registry.clear()

        registry.increment("metric")
from __future__ import annotations

from nova.metrics.exporters import ConsoleExporter
from nova.metrics.exporters import NullExporter


class TestNullExporter:
    def test_increment(self) -> None:
        exporter = NullExporter()

        exporter.increment("metric", 1)

    def test_timing(self) -> None:
        exporter = NullExporter()

        exporter.timing("metric", 10.5)


class TestConsoleExporter:
    def test_increment(self) -> None:
        exporter = ConsoleExporter()

        exporter.increment("cache.hit", 2)

    def test_timing(self) -> None:
        exporter = ConsoleExporter()

        exporter.timing("query", 4.5)
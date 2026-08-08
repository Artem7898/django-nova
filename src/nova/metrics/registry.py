"""
Central metrics registry.

The registry is the single source of truth for all runtime metrics inside
Django Nova.

Design goals
------------
- Thread-safe.
- Zero external dependencies.
- Supports multiple exporters.
- Supports counters and timers.
- Ready for OpenTelemetry Metrics bridge.
"""

from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nova.metrics.exporters import MetricsExporter


class MetricsRegistry:
    """
    Central registry for all Nova metrics.

    The registry owns exporters and dispatches metric events
    to every configured backend.

    The registry intentionally does not store metric values.
    Storage belongs to exporters.
    """

    def __init__(self) -> None:
        self._exporters: list[MetricsExporter] = []
        self._lock = Lock()

    def register_exporter(
        self,
        exporter: MetricsExporter,
    ) -> None:
        """
        Register a metrics exporter.

        Duplicate exporters are ignored.
        """

        with self._lock:
            if exporter not in self._exporters:
                self._exporters.append(exporter)

    def unregister_exporter(
        self,
        exporter: MetricsExporter,
    ) -> None:
        """
        Remove exporter.
        """

        with self._lock:
            if exporter in self._exporters:
                self._exporters.remove(exporter)

    def clear(self) -> None:
        """
        Remove every exporter.
        """

        with self._lock:
            self._exporters.clear()

    def increment(
        self,
        metric: str,
        value: int = 1,
    ) -> None:
        """
        Emit counter increment.
        """

        for exporter in tuple(self._exporters):
            exporter.increment(metric, value)

    def timing(
        self,
        metric: str,
        duration_ms: float,
    ) -> None:
        """
        Emit timing metric.
        """

        for exporter in tuple(self._exporters):
            exporter.timing(metric, duration_ms)


_registry: MetricsRegistry | None = None


def get_metrics_registry() -> MetricsRegistry:
    """
    Return the global metrics registry.
    """

    global _registry

    if _registry is None:
        _registry = MetricsRegistry()

    return _registry

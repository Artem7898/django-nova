"""
Metrics exporters.

Exporters receive metric events from the central registry.

Different exporters may send metrics to:

- stdout
- OpenTelemetry
- Prometheus
- Datadog
- Grafana
- custom monitoring systems
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from nova.core.observability import get_logger

logger = get_logger(__name__)


class MetricsExporter(ABC):
    """
    Base exporter interface.
    """

    @abstractmethod
    def increment(
        self,
        metric: str,
        value: int,
    ) -> None:
        """
        Export counter increment.
        """

    @abstractmethod
    def timing(
        self,
        metric: str,
        duration_ms: float,
    ) -> None:
        """
        Export duration metric.
        """


class NullExporter(MetricsExporter):
    """
    Metrics sink.

    Used when metrics are disabled.
    """

    def increment(
        self,
        metric: str,
        value: int,
    ) -> None:
        return

    def timing(
        self,
        metric: str,
        duration_ms: float,
    ) -> None:
        return


class ConsoleExporter(MetricsExporter):
    """
    Structured logging exporter.

    Useful for local development.
    """

    def increment(
        self,
        metric: str,
        value: int,
    ) -> None:
        logger.info(
            "metric_counter",
            metric=metric,
            value=value,
        )

    def timing(
        self,
        metric: str,
        duration_ms: float,
    ) -> None:
        logger.info(
            "metric_timer",
            metric=metric,
            duration_ms=duration_ms,
        )
"""
Timer metrics.

Timers measure execution duration in milliseconds.

Designed to integrate with OpenTelemetry Metrics and Prometheus Histograms.
"""

from __future__ import annotations

from contextlib import ContextDecorator
from time import perf_counter
from typing import Any

from nova.metrics.registry import get_metrics_registry


class Timer(ContextDecorator):
    """
    Context manager for timing code blocks.

    Example
    -------
    with Timer("nova.cache.lookup"):
        ...
    """

    def __init__(
        self,
        metric: str,
    ) -> None:
        self.metric = metric
        self._start = 0.0

    def __enter__(self) -> Timer:
        self._start = perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        elapsed = (perf_counter() - self._start) * 1000.0

        get_metrics_registry().timing(
            self.metric,
            elapsed,
        )

        return
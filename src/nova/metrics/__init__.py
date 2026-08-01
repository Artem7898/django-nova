"""Performance Counters and Metrics Registry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "Counter",
    "MetricsRegistry",
    "Timer",
]

def __getattr__(name: str) -> Any:
    if name == "MetricsRegistry":
        from nova.metrics.registry import MetricsRegistry
        return MetricsRegistry
    if name == "Counter":
        from nova.metrics.counters import Counter
        return Counter
    if name == "Timer":
        from nova.metrics.timers import Timer
        return Timer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from nova.metrics.counters import Counter
    from nova.metrics.registry import MetricsRegistry
    from nova.metrics.timers import Timer
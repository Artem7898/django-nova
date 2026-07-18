"""
Counter metrics.

Counters represent monotonically increasing values.

Examples
--------
nova.cache.hit
nova.cache.miss
nova.validation.failed
nova.task.started
"""

from __future__ import annotations

from enum import StrEnum

from nova.metrics.registry import get_metrics_registry


class Counter(StrEnum):
    """
    Built-in Nova counters.
    """

    CACHE_HIT = "nova.cache.hit"
    CACHE_MISS = "nova.cache.miss"

    CACHE_INVALIDATION = "nova.cache.invalidate"

    MODEL_SAVE = "nova.model.save"

    VALIDATION_SUCCESS = "nova.validation.success"
    VALIDATION_FAILED = "nova.validation.failed"

    TASK_SUBMITTED = "nova.task.submitted"
    TASK_STARTED = "nova.task.started"
    TASK_SUCCESS = "nova.task.success"
    TASK_FAILED = "nova.task.failed"


def increment(
    counter: Counter,
    value: int = 1,
) -> None:
    """
    Increment a counter.
    """

    get_metrics_registry().increment(counter.value, value)
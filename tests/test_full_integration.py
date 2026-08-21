from __future__ import annotations

import asyncio

import pytest

from nova.cache.invalidation import connect_invalidation
from nova.cache.queryset_cache import QuerySetCache
from nova.core.exceptions import NovaValidationError
from nova.tasks.engine import NovaTaskEngine
from tests.models import Lab


@pytest.mark.django_db
def test_full_lifecycle():
    # 1. Validation blocks bad data
    lab = Lab(name="Lab-1", budget=-10)
    with pytest.raises(NovaValidationError):
        lab.save()

    # 2. Accepts good data
    lab.budget = 1000.0
    lab.save()
    assert lab.pk is not None

    # 3. Cache works
    cache = QuerySetCache[Lab]()
    connect_invalidation(Lab, cache=cache)

    res1 = cache.get_or_set(Lab.objects.filter(pk=lab.pk))
    res2 = cache.get_or_set(Lab.objects.filter(pk=lab.pk))
    assert res1[0].pk == res2[0].pk
    assert cache.stats["currsize"] == 1

    # 4. Invalidation works on save
    lab.save()
    assert cache.stats["currsize"] == 0


@pytest.mark.asyncio
async def test_task_engine():
    # Старый тест остается без изменений (обратная совместимость)
    engine = NovaTaskEngine(max_concurrent=1)
    await engine.start()

    async def dummy_task(x: int) -> int:
        await asyncio.sleep(0.01)
        return x * 2

    task_id = engine.submit(dummy_task, 21)
    await asyncio.sleep(0.1)

    status = engine.get_status(task_id)
    assert status is not None
    assert status.status == "SUCCESS"
    assert status.result == 42

    await engine.stop()


@pytest.mark.asyncio
async def test_task_delayed_execution():
    """we verify that the task has delay=0.2 is not executed immediately."""
    engine = NovaTaskEngine(max_concurrent=1)
    await engine.start()

    execution_time = None

    async def track_time_task():
        nonlocal execution_time
        execution_time = time.time()
        return "done"

    import time

    start = time.time()
    task_id = engine.submit(track_time_task, delay=0.2)

    # We check that the task is still in the PENDING status after 0.1 seconds.
    await asyncio.sleep(0.1)
    status = engine.get_status(task_id)
    assert status.status == "PENDING"
    assert execution_time is None

    # Waiting for completion
    await asyncio.sleep(0.3)
    status = engine.get_status(task_id)
    assert status.status == "SUCCESS"
    assert (execution_time - start) >= 0.19  # Error per asink cycle

    await engine.stop()


@pytest.mark.asyncio
async def test_task_retry_mechanism():
    """We check that the task repeats in case of an error and marks the number of attempts."""
    engine = NovaTaskEngine(max_concurrent=1)
    await engine.start()

    attempt_counter = 0

    async def flaky_task():
        nonlocal attempt_counter
        attempt_counter += 1
        if attempt_counter < 3:
            raise ValueError("Not yet!")
        return "success"

    # We start with 2 retreats (3 attempts in total)
    task_id = engine.submit(flaky_task, max_retries=2, retry_delay=0.05)

    await asyncio.sleep(0.5)  # We give you time for 3 attempts and delays

    status = engine.get_status(task_id)
    assert status.status == "SUCCESS"
    assert status.result == "success"
    assert status.attempts == 3  # I made it on the third attempt

    await engine.stop()


@pytest.mark.asyncio
async def test_task_permanent_failure():
    """We check that after running out of retries, the task crashes with FAILED."""
    engine = NovaTaskEngine(max_concurrent=1)
    await engine.start()

    async def bad_task():
        raise RuntimeError("Fatal error")

    task_id = engine.submit(bad_task, max_retries=1, retry_delay=0.05)

    await asyncio.sleep(0.2)

    status = engine.get_status(task_id)
    assert status.status == "FAILED"
    assert "Fatal error" in status.error
    assert status.attempts == 2  # 1 original + 1 repeat

    await engine.stop()

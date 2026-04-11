from __future__ import annotations

import asyncio
import pytest
from nova.cache.queryset_cache import QuerySetCache
from nova.cache.invalidation import connect_invalidation
from nova.tasks.engine import NovaTaskEngine
from tests.models import Lab  # <-- Импортируем отсюда


@pytest.mark.django_db
def test_full_lifecycle():
    # 1. Validation blocks bad data
    lab = Lab(name="Lab-1", budget=-10)
    with pytest.raises(Exception):
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
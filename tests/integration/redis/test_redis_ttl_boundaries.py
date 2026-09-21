"""Non-positive TTL on real sync and async Redis; opt-in fixtures handle cleanup."""

from datetime import timedelta

import pytest

from tests.integration.redis import test_async_redis_integration as async_tests
from tests.integration.redis import test_sync_redis_integration as sync_tests

# Reuse the opt-in fixtures and their namespace cleanup, without collecting
# the original modules' tests a second time.
sync_case = sync_tests.redis_case
async_case = async_tests.redis_case

NON_POSITIVE = [0, -1, -0.0001, timedelta(0), timedelta(microseconds=-1)]


@pytest.mark.parametrize("ttl", NON_POSITIVE)
@pytest.mark.parametrize("bulk", [False, True], ids=["set", "set-many"])
def test_sync_non_positive_ttl(sync_case, ttl, bulk):
    backend = sync_case.backend
    backend.set("existing", "old", ttl=60)
    backend.set("neighbor", "keep", ttl=60)
    if bulk:
        backend.set_many({"existing": "new", "missing": "new"}, ttl=ttl)
    else:
        backend.set("existing", "new", ttl=ttl)
        backend.set("missing", "new", ttl=ttl)
    sentinel = object()
    for key in ("existing", "missing"):
        assert backend.get(key, sentinel) is sentinel
        assert sync_case.client.exists(f"{sync_case.prefix}:{key}") == 0
    assert backend.get("neighbor") == "keep"


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl", NON_POSITIVE)
@pytest.mark.parametrize("bulk", [False, True], ids=["set", "set-many"])
async def test_async_non_positive_ttl(async_case, ttl, bulk):
    backend = async_case.backend
    await backend.set("existing", "old", ttl=60)
    await backend.set("neighbor", "keep", ttl=60)
    if bulk:
        await backend.set_many({"existing": "new", "missing": "new"}, ttl=ttl)
    else:
        await backend.set("existing", "new", ttl=ttl)
        await backend.set("missing", "new", ttl=ttl)
    sentinel = object()
    for key in ("existing", "missing"):
        assert await backend.get(key, sentinel) is sentinel
        assert await async_case.client.exists(f"{async_case.prefix}:{key}") == 0
    assert await backend.get("neighbor") == "keep"

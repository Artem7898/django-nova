"""Tests for the asynchronous in-memory cache backend."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import timedelta

from nova.cache.backends.asyncio_backend import AsyncIOCacheBackend
from nova.cache.backends.protocol import AsyncCacheBackend


def run(coro: object) -> object:
    """Run an async operation without requiring pytest-asyncio."""
    return asyncio.run(coro)  # type: ignore[arg-type]


class TestAsyncIOCacheBackend:
    """Tests for AsyncIOCacheBackend."""

    def test_implements_async_cache_backend_protocol(self) -> None:
        assert AsyncCacheBackend in AsyncIOCacheBackend.__bases__

    def test_default_configuration(self) -> None:
        backend = AsyncIOCacheBackend()

        assert backend.backend_name == "asyncio"
        assert backend.supports_ttl is False
        assert backend.supports_atomic_increment is False
        assert backend.supports_pattern_delete is False
        assert backend.size() == 0

    def test_get_returns_default_for_missing_key(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            assert await backend.get("missing") is None
            assert await backend.get("missing", "fallback") == "fallback"

        run(scenario())

    def test_set_and_get(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            await backend.set("key", "value")

            assert await backend.get("key") == "value"
            assert backend.size() == 1

        run(scenario())

    def test_set_replaces_existing_value_without_increasing_size(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            await backend.set("key", "first")
            await backend.set("key", "second")

            assert await backend.get("key") == "second"
            assert backend.size() == 1

        run(scenario())

    def test_set_accepts_ttl_but_does_not_apply_expiration(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            await backend.set("key", "value", ttl=60)

            assert await backend.get("key") == "value"

            await backend.set(
                "timedelta-key",
                "timedelta-value",
                ttl=timedelta(seconds=1),
            )

            assert await backend.get("timedelta-key") == "timedelta-value"

        run(scenario())

    def test_delete_existing_key_returns_true(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            await backend.set("key", "value")

            assert await backend.delete("key") is True
            assert await backend.get("key") is None
            assert backend.size() == 0

        run(scenario())

    def test_delete_missing_key_returns_false(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            assert await backend.delete("missing") is False
            assert backend.size() == 0

        run(scenario())

    def test_clear_removes_all_entries(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            await backend.set("first", 1)
            await backend.set("second", 2)
            await backend.set("third", 3)

            assert backend.size() == 3

            await backend.clear()

            assert backend.size() == 0
            assert await backend.get("first") is None
            assert await backend.get("second") is None
            assert await backend.get("third") is None

        run(scenario())

    def test_get_many_returns_values_for_requested_keys(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            await backend.set("first", 1)
            await backend.set("second", 2)

            result = await backend.get_many(
                ["first", "second"],
            )

            assert isinstance(result, Mapping)
            assert dict(result) == {
                "first": 1,
                "second": 2,
            }

        run(scenario())

    def test_get_many_returns_none_for_missing_keys(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            await backend.set("existing", "value")

            result = await backend.get_many(
                ["existing", "missing"],
            )

            assert dict(result) == {
                "existing": "value",
                "missing": None,
            }

        run(scenario())

    def test_get_many_preserves_requested_key_order(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            await backend.set("first", 1)
            await backend.set("second", 2)
            await backend.set("third", 3)

            result = await backend.get_many(
                ["third", "first", "missing"],
            )

            assert list(result.keys()) == [
                "third",
                "first",
                "missing",
            ]

        run(scenario())

    def test_set_many_stores_all_values(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            values = {
                "first": 1,
                "second": 2,
                "third": 3,
            }

            await backend.set_many(values)

            assert backend.size() == 3
            assert dict(await backend.get_many(list(values))) == values

        run(scenario())

    def test_set_many_accepts_ttl_without_expiration(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            values = {
                "first": "one",
                "second": "two",
            }

            await backend.set_many(
                values,
                ttl=timedelta(seconds=30),
            )

            assert dict(await backend.get_many(list(values))) == values

        run(scenario())

    def test_set_many_replaces_existing_values(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            await backend.set("first", "old")
            await backend.set("second", "keep")

            await backend.set_many(
                {
                    "first": "new",
                    "third": "added",
                },
            )

            assert backend.size() == 3
            assert await backend.get("first") == "new"
            assert await backend.get("second") == "keep"
            assert await backend.get("third") == "added"

        run(scenario())

    def test_delete_many_returns_number_of_deleted_keys(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            await backend.set_many(
                {
                    "first": 1,
                    "second": 2,
                    "third": 3,
                },
            )

            deleted = await backend.delete_many(
                ["first", "missing", "third"],
            )

            assert deleted == 2
            assert backend.size() == 1
            assert await backend.get("second") == 2

        run(scenario())

    def test_delete_many_returns_zero_when_no_keys_exist(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            deleted = await backend.delete_many(
                ["missing-one", "missing-two"],
            )

            assert deleted == 0
            assert backend.size() == 0

        run(scenario())

    def test_delete_many_does_not_count_duplicate_key_twice(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            await backend.set("key", "value")

            deleted = await backend.delete_many(
                ["key", "key"],
            )

            assert deleted == 1
            assert backend.size() == 0

        run(scenario())

    def test_fifo_eviction_removes_oldest_key(self) -> None:
        backend = AsyncIOCacheBackend(maxsize=2)

        async def scenario() -> None:
            await backend.set("first", 1)
            await backend.set("second", 2)

            assert backend.size() == 2

            await backend.set("third", 3)

            assert backend.size() == 2
            assert await backend.get("first") is None
            assert await backend.get("second") == 2
            assert await backend.get("third") == 3

        run(scenario())

    def test_updating_existing_key_does_not_trigger_eviction(self) -> None:
        backend = AsyncIOCacheBackend(maxsize=2)

        async def scenario() -> None:
            await backend.set("first", 1)
            await backend.set("second", 2)

            await backend.set("first", 100)

            assert backend.size() == 2
            assert await backend.get("first") == 100
            assert await backend.get("second") == 2

        run(scenario())

    def test_deleting_key_creates_capacity_without_eviction(self) -> None:
        backend = AsyncIOCacheBackend(maxsize=3)

        async def scenario() -> None:
            await backend.set("first", 1)
            await backend.set("second", 2)
            await backend.set("third", 3)

            await backend.delete("first")
            await backend.set("fourth", 4)

            assert backend.size() == 3
            assert await backend.get("first") is None
            assert await backend.get("second") == 2
            assert await backend.get("third") == 3
            assert await backend.get("fourth") == 4

        run(scenario())

    def test_maxsize_one_keeps_only_latest_new_key(self) -> None:
        backend = AsyncIOCacheBackend(maxsize=1)

        async def scenario() -> None:
            await backend.set("first", 1)
            await backend.set("second", 2)

            assert backend.size() == 1
            assert await backend.get("first") is None
            assert await backend.get("second") == 2

        run(scenario())

    def test_size_tracks_insertions_and_deletions(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            assert backend.size() == 0

            await backend.set("first", 1)
            assert backend.size() == 1

            await backend.set("second", 2)
            assert backend.size() == 2

            await backend.delete("first")
            assert backend.size() == 1

            await backend.clear()
            assert backend.size() == 0

        run(scenario())

    def test_backend_handles_none_as_cached_value(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            await backend.set("key", None)

            assert await backend.get("key") is None
            assert backend.size() == 1

            assert await backend.delete("key") is True

        run(scenario())

    def test_backend_handles_complex_python_values(self) -> None:
        backend = AsyncIOCacheBackend()

        async def scenario() -> None:
            value = {
                "items": [1, 2, 3],
                "nested": {"enabled": True},
            }

            await backend.set("complex", value)

            assert await backend.get("complex") == value

        run(scenario())

    def test_concurrent_writes_preserve_consistent_state(self) -> None:
        backend = AsyncIOCacheBackend(maxsize=100)

        async def scenario() -> None:
            async def write(index: int) -> None:
                await backend.set(
                    f"key-{index}",
                    index,
                )

            await asyncio.gather(
                *(write(index) for index in range(50)),
            )

            assert backend.size() == 50

            result = await backend.get_many(
                [f"key-{index}" for index in range(50)],
            )

            assert len(result) == 50

            for index in range(50):
                assert result[f"key-{index}"] == index

        run(scenario())

    def test_concurrent_reads_and_writes_remain_consistent(self) -> None:
        backend = AsyncIOCacheBackend(maxsize=100)

        async def scenario() -> None:
            await backend.set("counter", 0)

            async def writer() -> None:
                for value in range(20):
                    await backend.set("counter", value)
                    await asyncio.sleep(0)

            async def reader() -> list[object]:
                values: list[object] = []

                for _ in range(20):
                    values.append(await backend.get("counter"))
                    await asyncio.sleep(0)

                return values

            readers = await asyncio.gather(
                reader(),
                reader(),
                writer(),
            )

            read_values = readers[:2]

            for values in read_values:
                assert all(value is not None for value in values)

            final_value = await backend.get("counter")
            assert final_value == 19

        run(scenario())

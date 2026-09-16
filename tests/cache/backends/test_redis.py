"""Synchronous Redis backend contracts; no running Redis server required."""

from collections.abc import Iterator
from contextlib import ExitStack
from datetime import timedelta
from unittest.mock import MagicMock

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from tests.cache.contracts.base import CacheBackendContract, CacheBackendExpectation

from nova.cache.backends.redis import RedisCacheBackend
from nova.core.exceptions import NovaCacheError

CONTRACT_CHECKS = (
    "check_set_get_roundtrip",
    "check_missing_key_returns_default",
    "check_overwrite_last_write_wins",
    "check_delete_semantics",
    "check_clear",
    "check_bulk_consistency",
    "check_size_consistency",
    "check_backend_metadata",
    "check_capabilities_declared",
    "check_value_identity",
    "check_none_value_semantics",
    "check_ttl_semantics",
)


@pytest.fixture
def client() -> Iterator[fakeredis.FakeRedis]:
    with fakeredis.FakeRedis(decode_responses=False) as redis:
        yield redis


@pytest.mark.parametrize("check_name", CONTRACT_CHECKS)
def test_sync_redis_contract(check_name: str) -> None:
    with ExitStack() as stack:

        def factory() -> RedisCacheBackend:
            redis = stack.enter_context(fakeredis.FakeRedis(decode_responses=False))
            return RedisCacheBackend(client=redis, key_prefix="contract")

        contract = CacheBackendContract(
            CacheBackendExpectation(
                target="synchronous Redis cache",
                factory=factory,
                supports_ttl=True,
            ),
        )
        getattr(contract, check_name)()


def test_clear_preserves_other_namespaces(client: fakeredis.FakeRedis) -> None:
    first = RedisCacheBackend(client=client, key_prefix="nova")
    second = RedisCacheBackend(client=client, key_prefix="nova-other")
    first.set("key", 1)
    second.set("key", 2)
    client.set("external", b"preserve")

    first.clear()

    assert first.get("key") is None
    assert second.get("key") == 2
    assert client.get("external") == b"preserve"


@pytest.mark.parametrize(
    ("prefix", "other_prefix"),
    [
        ("tenant*", "tenant-other"),
        ("tenant?", "tenantX"),
        ("tenant[ab]", "tenanta"),
        (r"tenant\a", "tenanta"),
    ],
)
def test_clear_treats_prefix_literally(
    client: fakeredis.FakeRedis,
    prefix: str,
    other_prefix: str,
) -> None:
    own = RedisCacheBackend(client=client, key_prefix=prefix)
    other = RedisCacheBackend(client=client, key_prefix=other_prefix)
    own.set("key", 1)
    other.set("key", 2)

    own.clear()

    assert other.get("key") == 2, "clear() deleted another namespace"
    assert own.get("key") is None


def test_empty_prefix_supports_crud_but_refuses_clear(
    client: fakeredis.FakeRedis,
) -> None:
    backend = RedisCacheBackend(client=client, key_prefix="")
    backend.set("key", 1)
    assert backend.get("key") == 1
    with pytest.raises(NovaCacheError, match="key_prefix"):
        backend.clear()
    assert backend.get("key") == 1
    assert backend.delete("key") is True


def test_clear_scans_past_empty_pages() -> None:
    client = MagicMock()
    client.scan.side_effect = [
        (7, []),
        (9, [b"nova:a"]),
        (0, [b"nova:b"]),
    ]
    RedisCacheBackend(client=client).clear()
    assert [call.kwargs["cursor"] for call in client.scan.call_args_list] == [0, 7, 9]
    assert [call.args for call in client.delete.call_args_list] == [
        (b"nova:a",),
        (b"nova:b",),
    ]
    client.flushdb.assert_not_called()
    client.flushall.assert_not_called()


def test_empty_bulk_operations_do_not_contact_redis() -> None:
    client = MagicMock()
    backend = RedisCacheBackend(client=client)
    assert backend.get_many([]) == {}
    backend.set_many({})
    assert backend.delete_many([]) == 0
    assert client.mock_calls == []


@pytest.mark.parametrize("bulk", [False, True], ids=["set", "set-many"])
@pytest.mark.parametrize("ttl", [30, 30.5, timedelta(seconds=30)])
def test_positive_ttl_is_applied(
    client: fakeredis.FakeRedis,
    bulk: bool,
    ttl: float | timedelta,
) -> None:
    backend = RedisCacheBackend(client=client)
    if bulk:
        backend.set_many({"a": 1, "b": 2}, ttl=ttl)
    else:
        backend.set("a", 1, ttl=ttl)
    limit = int((ttl.total_seconds() if isinstance(ttl, timedelta) else ttl) * 1000)
    for key in ["a", "b"] if bulk else ["a"]:
        assert 0 < client.pttl(f"nova:{key}") <= limit
        assert backend.get(key) in (1, 2)


@pytest.mark.parametrize("bulk", [False, True], ids=["set", "set-many"])
def test_overwrite_without_ttl_removes_expiration(
    client: fakeredis.FakeRedis,
    bulk: bool,
) -> None:
    backend = RedisCacheBackend(client=client)
    backend.set("key", 1, ttl=60)
    if bulk:
        backend.set_many({"key": 2})
    else:
        backend.set("key", 2)
    assert backend.get("key") == 2
    assert client.pttl("nova:key") == -1


OPERATIONS = ("get", "set", "delete", "clear", "get_many", "set_many", "delete_many")


def invoke(backend: RedisCacheBackend, operation: str) -> object:
    if operation == "set":
        return backend.set("key", 1)
    if operation == "set_many":
        return backend.set_many({"key": 1})
    if operation in {"get_many", "delete_many"}:
        return getattr(backend, operation)(["key"])
    if operation == "clear":
        return backend.clear()
    return getattr(backend, operation)("key")


@pytest.mark.parametrize("operation", OPERATIONS)
def test_connection_failure_is_wrapped_with_cause(operation: str) -> None:
    server = fakeredis.FakeServer()
    with fakeredis.FakeRedis(server=server) as client:
        backend = RedisCacheBackend(client=client)
        server.connected = False
        with pytest.raises(NovaCacheError) as caught:
            invoke(backend, operation)
        assert isinstance(caught.value.__cause__, RedisConnectionError)


@pytest.mark.parametrize("operation", OPERATIONS)
def test_programming_errors_are_not_disguised(operation: str) -> None:
    client = MagicMock()
    command = {
        "get_many": "mget",
        "set_many": "pipeline",
        "delete_many": "delete",
        "clear": "scan",
    }.get(operation, operation)
    error = TypeError("unexpected application error")
    getattr(client, command).side_effect = error
    with pytest.raises(TypeError) as caught:
        invoke(RedisCacheBackend(client=client), operation)
    assert caught.value is error


def test_diagnostic_failure_returns_fallbacks() -> None:
    client = MagicMock()
    client.dbsize.side_effect = RedisConnectionError("offline")
    client.info.side_effect = RedisConnectionError("offline")
    backend = RedisCacheBackend(client=client)
    assert backend.size() == -1
    assert backend.stats() == {"backend": "redis", "status": "error"}

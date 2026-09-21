"""Real TCP, expiration and rolling-upgrade contracts; never flush the server.

Enable with NOVA_TEST_MEMCACHED_SERVER=127.0.0.1:51211.
When configured, unavailable dependencies or an unreachable server are errors.
"""

import os
import time
from contextlib import ExitStack
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from nova.cache.backends.memcached import MemcachedCacheBackend
from nova.cache.backends.serializers import PickleSerializer

pytestmark = pytest.mark.integration

LIMIT = 30 * 24 * 60 * 60


@pytest.fixture(scope="module")
def memcached_server():
    server = os.environ.get("NOVA_TEST_MEMCACHED_SERVER")
    if not server:
        pytest.skip("Set NOVA_TEST_MEMCACHED_SERVER to run real Memcached tests")
    return server


@pytest.fixture
def memcached_pair(memcached_server):
    with ExitStack() as cleanup:
        backends = []
        for _ in range(2):
            # Exercise the public constructor, not an injected fake/client.
            backend = MemcachedCacheBackend(server=memcached_server)
            client = backend._client
            client.connect_timeout = 3
            client.timeout = 3
            cleanup.callback(client.close)
            assert client.version(), "Memcached did not return a server version"
            backends.append(backend)
        yield tuple(backends)


@pytest.fixture
def key(memcached_pair):
    prefix = f"nova-it:{uuid4().hex}:"
    keys = set()

    def make_key(suffix):
        result = prefix + suffix
        keys.add(result)
        return result

    try:
        yield make_key
    finally:
        # Delete exactly this test's keys; other test runs remain untouched.
        client = memcached_pair[0]._client
        for name in keys:
            client.delete(name)


def wait_for_server_expiration(client, name, *, timeout=6):
    deadline = time.monotonic() + timeout
    while client.get(name) is not None:
        assert time.monotonic() < deadline, f"Server did not expire {name}"
        time.sleep(0.05)


def test_python_values_roundtrip_between_independent_clients(memcached_pair, key):
    writer, reader = memcached_pair
    sentinel = object()
    values = [
        None,
        False,
        0,
        "Нова",
        b"\x00\xff",
        {"price": Decimal("12.34"), "items": [1, 2]},
        date(2026, 1, 1),
        UUID("12345678-1234-5678-1234-567812345678"),
    ]
    for index, value in enumerate(values):
        name = key(str(index))
        writer.set(name, value)
        assert reader.get(name, sentinel) == value
    assert reader.get(key("missing"), sentinel) is sentinel


def test_bulk_and_acknowledged_delete_counts(memcached_pair, key):
    writer, reader = memcached_pair
    a, b, missing = key("a"), key("b"), key("missing")
    writer.set_many({a: 1, b: None})
    assert reader.get_many([a, b]) == {a: 1, b: None}
    assert reader.delete(a) is True
    assert reader.delete(a) is False
    assert reader.delete(missing) is False
    assert reader.delete_many([b, b, missing]) == 1
    assert reader.delete_many([a, b]) == 0


@pytest.mark.parametrize("bulk", [False, True], ids=["set", "set-many"])
def test_real_server_expiration(memcached_pair, key, bulk):
    writer, reader = memcached_pair
    name = key("expiring")
    if bulk:
        writer.set_many({name: "value"}, ttl=2)
    else:
        writer.set(name, "value", ttl=2)
    assert reader.get(name) == "value"
    # Raw GET proves that the server, not only the payload, has expired it.
    wait_for_server_expiration(reader._client, name)
    sentinel = object()
    assert reader.get(name, sentinel) is sentinel
    assert reader.delete(name) is False


@pytest.mark.parametrize("bulk", [False, True], ids=["set", "set-many"])
@pytest.mark.parametrize("ttl", [0, -1, -0.0001, timedelta(0), timedelta(microseconds=-1)])
def test_non_positive_ttl_removes_only_target_keys(memcached_pair, key, bulk, ttl):
    writer, reader = memcached_pair
    existing, missing, neighbor = key("existing"), key("missing"), key("neighbor")
    writer.set(existing, "old", ttl=60)
    writer.set(neighbor, "preserved", ttl=60)
    values = {existing: "new", missing: "new"}
    if bulk:
        writer.set_many(values, ttl=ttl)
    else:
        for name, value in values.items():
            writer.set(name, value, ttl=ttl)
    for name in values:
        assert reader._client.get(name) is None
        assert reader.delete(name) is False
    assert reader.get(neighbor) == "preserved"


@pytest.mark.parametrize("bulk", [False, True], ids=["set", "set-many"])
@pytest.mark.parametrize(
    "ttl",
    [LIMIT - 0.1, LIMIT, LIMIT + 0.1, timedelta(days=31), timedelta(days=365)],
    ids=["below-30d", "exactly-30d", "above-30d", "31d", "365d"],
)
def test_protocol_boundary_does_not_expire_long_ttl_immediately(memcached_pair, key, bulk, ttl):
    writer, reader = memcached_pair
    values = {key("a"): 1, key("b"): 2}
    if bulk:
        writer.set_many(values, ttl=ttl)
    else:
        for name, value in values.items():
            writer.set(name, value, ttl=ttl)
    # Relative values over 30 days would be interpreted as old Unix timestamps.
    # Exact wire deadlines are additionally checked by the expiration unit tests.
    assert reader.get_many(list(values)) == values
    for name in values:
        assert reader._client.get(name) is not None


@pytest.mark.parametrize("bulk", [False, True], ids=["set", "set-many"])
def test_persistent_overwrite_removes_both_deadlines(memcached_pair, key, bulk):
    writer, reader = memcached_pair
    name = key("persistent")
    writer.set(name, "old", ttl=2)
    if bulk:
        writer.set_many({name: "new"}, ttl=None)
    else:
        writer.set(name, "new", ttl=None)
    # Wait past the original deadline, using monotonic time only for the test.
    deadline = time.monotonic() + 2.1
    while time.monotonic() < deadline:
        assert reader.get(name) == "new"
        time.sleep(0.05)
    assert reader._client.get(name) is not None
    assert reader.get(name) == "new"


@pytest.mark.parametrize("value", [None, "legacy", {"items": [1, 2]}])
def test_legacy_persistent_payload_is_readable(memcached_pair, key, value):
    writer, reader = memcached_pair
    name = key("legacy")
    payload = PickleSerializer().dumps((None, value))
    assert writer._client.set(name, payload, expire=60)
    assert reader.get(name, object()) == value


@pytest.mark.parametrize("deadline", [1.0, 9_000_000_000.0])
def test_legacy_timed_payload_is_a_miss_without_read_side_delete(memcached_pair, key, deadline):
    writer, reader = memcached_pair
    name = key("legacy-timed")
    payload = PickleSerializer().dumps((deadline, "legacy"))
    assert writer._client.set(name, payload, expire=60)
    sentinel = object()
    assert reader.get(name, sentinel) is sentinel
    assert reader._client.get(name) == payload

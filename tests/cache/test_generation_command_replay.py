"""Applied writes with lost replies must never resurrect observed generations."""

from types import SimpleNamespace

import pytest

from nova.cache.backends.memcached import MemcachedCacheBackend
from nova.cache.backends.redis import RedisCacheBackend
from nova.cache.generation import GenerationUnavailableError, generation_key, generation_scope
from nova.cache.generation_transport import (
    MemcachedGenerationWriter,
    memcached_generation_writer,
    redis_generation_writer,
)
from nova.cache.queryset_cache import QuerySetCache
from tests.cache.test_queryset_cache_failure_recovery import QueryStub

SCOPE = generation_scope("record", "default")
WILDCARD = generation_scope("record", "*")


class StoreClient:
    """Minimal byte store with real SET / create-if-absent semantics."""

    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, *, nx=False, **kwargs):
        if nx and key in self.values:
            return False
        self.values[key] = value.encode("ascii") if isinstance(value, str) else value
        return True

    def add(self, key, value, **kwargs):
        return self.set(key, value, nx=True)

    def delete(self, key):
        return int(self.values.pop(key, None) is not None)


class AppliedWriteClient:
    """Run an interleaving after server apply, then lose or replay the reply.

    The callback executes once. Competing clients bypass this wrapper, so
    their commands cannot consume its fault. All replay arguments are intact.
    """

    def __init__(self, client):
        self.client = client
        self.fault = None
        self.applications = 0

    def arm(self, key, mode, callback=lambda: None):
        self.fault = (key, mode, callback)
        self.applications = 0

    def get(self, key):
        return self.client.get(key)

    def delete(self, key):
        return self.client.delete(key)

    def set(self, key, value, **kwargs):
        return self._write("set", key, value, kwargs)

    def add(self, key, value, **kwargs):
        return self._write("add", key, value, kwargs)

    def _write(self, method, key, value, kwargs):
        command = getattr(self.client, method)
        result = command(key, value, **kwargs)
        if self.fault is None or self.fault[0] != key:
            return result
        _, mode, callback = self.fault
        self.fault = None
        assert result is True, "The first write must have reached the server"
        self.applications += 1
        callback()
        if mode == "lose-reply":
            raise OSError("Injected: write applied, acknowledgement lost")
        if mode == "replay":
            result = command(key, value, **kwargs)
            self.applications += 1
        return result


class AppliedWriteWriter:
    """A single-attempt boundary: a lost acknowledgement is always an error."""

    def __init__(self, writer):
        self.writer = writer
        self.fault = None
        self.applications = 0

    def arm(self, key, mode, callback=lambda: None):
        self.fault = (key, mode, callback)
        self.applications = 0

    def write(self, key, token, *, only_if_absent):
        result = self.writer.write(key, token, only_if_absent=only_if_absent)
        if self.fault is not None and self.fault[0] == key:
            _, mode, callback = self.fault
            self.fault = None
            assert result is True
            self.applications += 1
            callback()
            if mode != "acknowledge":
                raise OSError("Injected: write applied, acknowledgement lost")
        return result


def make_case(kind, first, second, *, prefix=""):
    def writer(client):
        if isinstance(client, StoreClient):
            return MemcachedGenerationWriter(client)
        factory = redis_generation_writer if kind == "redis" else memcached_generation_writer
        return factory(client)

    transport = AppliedWriteWriter(writer(first))

    def backend(client, generation_writer):
        if kind == "redis":
            return RedisCacheBackend(
                client=client, key_prefix=prefix, generation_writer=generation_writer
            )
        return MemcachedCacheBackend(client=client, generation_writer=generation_writer)

    first_backend, second_backend = backend(first, transport), backend(second, writer(second))
    metadata = generation_key(SCOPE)
    if kind == "redis" and prefix:
        metadata = f"{prefix}:{metadata}"
    return SimpleNamespace(
        kind=kind,
        first_client=first,
        second_client=second,
        transport=transport,
        backend=first_backend,
        peer=second_backend,
        metadata=metadata,
        reader=QuerySetCache(backend=second_backend),
        keys=set(),
    )


@pytest.fixture(params=["redis", "memcached"])
def replay_case(request):
    store = StoreClient()
    return make_case(request.param, store, store)


def cache_value(case, value):
    query = QueryStub(value)
    case.keys.add(case.reader._generate_key(query)[0])
    assert case.reader.get_or_set(query) == [value]


def run_rotation_interleaving(case, mode):
    cache_value(case, 1)
    observed = {}

    def another_writer_commits():
        observed["early"] = case.peer.get_generation(SCOPE)
        cache_value(case, 2)
        observed["old_key"] = case.reader._generate_key(QueryStub())[0]
        # A second writer commits value=3 and successfully invalidates, while
        # the first writer still waits for its generation-write response.
        observed["latest"] = case.peer.rotate_generation(SCOPE)
        assert observed["latest"] != observed["early"]
        assert case.reader.get(QueryStub()) is None

    case.transport.arm(case.metadata, mode, another_writer_commits)
    if mode == "acknowledge":
        case.backend.rotate_generation(SCOPE)
    else:
        with pytest.raises(GenerationUnavailableError):
            case.backend.rotate_generation(SCOPE)
    assert case.peer.get(observed["old_key"]) == [2], "Keep stale data present as a control"
    fresh = QueryStub(3)
    case.keys.add(case.reader._generate_key(fresh)[0])
    result = case.reader.get_or_set(fresh)
    assert case.transport.applications == 1
    assert result == [3], (
        "A repeated generation SET revived a token observed before the latest "
        f"invalidation: got {result}; query executions={fresh.executions}"
    )
    assert fresh.executions == 1


def run_initialization_interleaving(case, mode):
    case.peer.get_generation(WILDCARD)
    case.peer.delete(generation_key(SCOPE))
    observed = {}

    def evict_after_another_commit():
        cache_value(case, 1)
        observed["old_key"] = case.reader._generate_key(QueryStub())[0]
        case.peer.rotate_generation(SCOPE)  # Another writer commits value=2.
        case.peer.delete(generation_key(SCOPE))  # Only metadata is evicted.

    case.transport.arm(case.metadata, mode, evict_after_another_commit)
    if mode == "acknowledge":
        case.backend.get_generation(SCOPE)
    else:
        with pytest.raises(GenerationUnavailableError):
            case.backend.get_generation(SCOPE)
    assert case.peer.get(observed["old_key"]) == [1]
    fresh = QueryStub(2)
    case.keys.add(case.reader._generate_key(fresh)[0])
    result = case.reader.get_or_set(fresh)
    assert case.transport.applications == 1
    assert result == [2], (
        "Replayed create-if-absent reused an observed token after eviction: "
        f"got {result}; query executions={fresh.executions}"
    )
    assert fresh.executions == 1


def test_lost_rotation_reply_does_not_revive_stale_results(replay_case):
    run_rotation_interleaving(replay_case, "lose-reply")


def test_rotation_without_replay_preserves_newer_invalidation(replay_case):
    run_rotation_interleaving(replay_case, "acknowledge")


def test_lost_initialization_reply_after_eviction_does_not_revive_results(replay_case):
    run_initialization_interleaving(replay_case, "lose-reply")


def test_initialization_without_replay_uses_a_fresh_token_after_eviction(replay_case):
    run_initialization_interleaving(replay_case, "acknowledge")


def test_applied_rotation_with_lost_reply_is_reported_and_retry_is_fresh(replay_case):
    case = replay_case
    cache_value(case, 1)
    old = case.peer.get_generation(SCOPE)
    case.transport.arm(case.metadata, "lose-reply")
    with pytest.raises(GenerationUnavailableError) as raised:
        case.backend.rotate_generation(SCOPE)
    assert isinstance(raised.value.__cause__, OSError)
    applied = case.peer.get_generation(SCOPE)
    assert applied != old
    assert case.reader.get(QueryStub()) is None
    # Retrying Nova's whole operation must generate a NEW candidate.
    retried = case.backend.rotate_generation(SCOPE)
    assert retried not in {old, applied}
    cache_value(case, 2)


def test_applied_initialization_with_lost_reply_is_not_returned_as_success(replay_case):
    case = replay_case
    case.transport.arm(case.metadata, "lose-reply")
    with pytest.raises(GenerationUnavailableError) as raised:
        case.backend.get_generation(SCOPE)
    assert isinstance(raised.value.__cause__, OSError)
    # Once a fresh read confirms it, this token is usable: no eviction or
    # intervening rotation occurred, and no old command was replayed.
    applied = case.peer.get_generation(SCOPE)
    assert case.backend.get_generation(SCOPE) == applied


@pytest.mark.parametrize("kind", ["redis", "memcached"])
def test_unknown_retrying_client_is_rejected_before_reading_or_writing_metadata(kind):
    store = StoreClient()
    unsafe = AppliedWriteClient(store)
    unsafe.arm(generation_key(SCOPE), "replay")
    backend = (
        RedisCacheBackend(client=unsafe, key_prefix="")
        if kind == "redis"
        else MemcachedCacheBackend(client=unsafe)
    )
    with pytest.raises(GenerationUnavailableError, match="single-attempt"):
        backend.get_generation(SCOPE)
    with pytest.raises(GenerationUnavailableError, match="single-attempt"):
        backend.rotate_generation(SCOPE)
    assert unsafe.applications == 0
    cache = QuerySetCache(backend=backend)
    query = QueryStub(3)
    assert cache.get_or_set(query) == [3]
    assert query.executions == 1
    assert store.values == {}


@pytest.mark.parametrize("kind", ["redis", "memcached"])
def test_explicit_writer_bypasses_retrying_data_client(kind):
    store = StoreClient()
    unsafe = AppliedWriteClient(store)
    unsafe.arm(generation_key(SCOPE), "replay")
    writer = MemcachedGenerationWriter(store)
    backend = (
        RedisCacheBackend(client=unsafe, key_prefix="", generation_writer=writer)
        if kind == "redis"
        else MemcachedCacheBackend(client=unsafe, generation_writer=writer)
    )
    old = backend.get_generation(SCOPE)
    new = backend.rotate_generation(SCOPE)
    assert new != old
    assert backend.get_generation(SCOPE) == new
    assert unsafe.applications == 0

"""Batched generation reads preserve freshness, recovery and legacy clients."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nova.cache.backends.memcached import MemcachedCacheBackend
from nova.cache.backends.redis import RedisCacheBackend
from nova.cache.generation import (
    GenerationUnavailableError,
    generation_key,
    generation_scope,
    new_generation,
)
from nova.cache.queryset_cache import QuerySetCache
from tests.cache.test_cache_generation_backends import MetadataClient, MetadataWriter
from tests.cache.test_queryset_cache_failure_recovery import QueryStub
from tests.cache.test_shared_cache_generation import SharedBackend


class BatchClient(MetadataClient):
    def __init__(self):
        super().__init__()
        self.reads = []

    def get(self, key):
        self.reads.append(("get", (key,)))
        return super().get(key)

    def mget(self, keys):
        self.reads.append(("mget", tuple(keys)))
        if self.error:
            raise self.error
        return [self.values.get(key) for key in keys]

    def get_many(self, keys):
        self.reads.append(("get_many", tuple(keys)))
        if self.error:
            raise self.error
        # Deliberately reverse response order; callers must match keys.
        return {key: self.values[key] for key in reversed(keys) if key in self.values}


@pytest.fixture(params=["redis", "memcached"])
def batch_case(request):
    kind = request.param
    client = BatchClient()
    writer = MetadataWriter(client, kind)
    backend = (
        RedisCacheBackend(client=client, key_prefix="batch-test", generation_writer=writer)
        if kind == "redis"
        else MemcachedCacheBackend(client=client, generation_writer=writer)
    )
    return SimpleNamespace(
        backend=backend,
        client=client,
        writer=writer,
        kind=kind,
        method="mget" if kind == "redis" else "get_many",
        key=lambda scope: (
            f"batch-test:{generation_key(scope)}" if kind == "redis" else generation_key(scope)
        ),
    )


SCOPES = tuple(generation_scope("record", alias) for alias in ("*", "default", "replica"))


def seed(case):
    tokens = {scope: new_generation() for scope in SCOPES}
    case.client.values.update(
        {case.key(scope): token.encode("ascii") for scope, token in tokens.items()}
    )
    return tokens


def test_warm_generations_use_one_read_and_no_writes(batch_case):
    case = batch_case
    expected = seed(case)
    assert case.backend.get_generations(SCOPES) == expected
    assert case.client.reads == [(case.method, tuple(case.key(scope) for scope in SCOPES))]
    assert case.client.calls == []


def test_empty_batch_does_not_touch_transport(batch_case):
    case = batch_case
    case.client.error = OSError("must not connect")
    assert case.backend.get_generations(()) == {}
    assert case.client.reads == case.client.calls == []


def test_duplicate_scopes_do_not_duplicate_reads(batch_case):
    case = batch_case
    expected = seed(case)
    assert case.backend.get_generations((*SCOPES, SCOPES[0])) == expected
    assert len(case.client.reads[0][1]) == len(SCOPES)


def test_partial_miss_preserves_existing_tokens_and_initializes_only_missing(batch_case):
    case = batch_case
    expected = seed(case)
    missing = SCOPES[1]
    del case.client.values[case.key(missing)]
    actual = case.backend.get_generations(SCOPES)
    assert actual[missing] != expected[missing]
    assert {scope: actual[scope] for scope in (SCOPES[0], SCOPES[2])} == {
        scope: expected[scope] for scope in (SCOPES[0], SCOPES[2])
    }
    assert [read[1] for read in case.client.reads[1:]] == [(case.key(missing),)] * 2
    assert len(case.client.calls) == 1
    assert case.client.calls[0][1:] == (True, 0, False)


def test_missing_token_initialization_cannot_replace_concurrent_rotation(batch_case):
    case = batch_case
    rotated = []
    case.client.before_create = lambda: rotated.append(case.backend.rotate_generation(SCOPES[0]))
    assert case.backend.get_generations((SCOPES[0],)) == {SCOPES[0]: rotated[0]}


@pytest.mark.parametrize("raw", [b"bad", b"\xff", 42, "z" * 32])
def test_corruption_aborts_batch_before_initializing_another_scope(batch_case, raw):
    case = batch_case
    case.client.values[case.key(SCOPES[1])] = raw
    with pytest.raises(GenerationUnavailableError):
        case.backend.get_generations(SCOPES)
    assert case.client.calls == []
    assert case.client.values == {case.key(SCOPES[1]): raw}


def test_batch_transport_error_is_not_hidden_by_scalar_retry(batch_case):
    case = batch_case
    error = OSError("batch reply lost")
    case.client.error = error
    with pytest.raises(GenerationUnavailableError) as caught:
        case.backend.get_generations(SCOPES)
    assert caught.value.__cause__ is error
    assert len(case.client.reads) == 1
    assert case.client.calls == []


def test_malformed_batch_response_is_rejected(batch_case, monkeypatch):
    case = batch_case
    monkeypatch.setattr(case.client, case.method, lambda keys: None)
    with pytest.raises(GenerationUnavailableError):
        case.backend.get_generations(SCOPES)
    assert case.client.calls == []


def test_initialization_lost_ack_is_not_replayed(batch_case, monkeypatch):
    case = batch_case
    write = case.writer.write

    def lose_ack(*args, **kwargs):
        assert write(*args, **kwargs) is True
        raise OSError("applied, acknowledgement lost")

    monkeypatch.setattr(case.writer, "write", lose_ack)
    with pytest.raises(GenerationUnavailableError):
        case.backend.get_generations((SCOPES[0],))
    assert len(case.client.calls) == 1
    applied = case.client.values[case.key(SCOPES[0])].decode("ascii")
    assert case.backend.get_generations((SCOPES[0],)) == {SCOPES[0]: applied}
    assert len(case.client.calls) == 1


def test_legacy_injected_client_without_batch_method_still_works(batch_case, monkeypatch):
    case = batch_case
    expected = seed(case)
    monkeypatch.setattr(case.client, case.method, None)
    assert case.backend.get_generations(SCOPES) == expected
    assert [name for name, _ in case.client.reads] == ["get"] * len(SCOPES)


def test_unrecognized_writer_transport_remains_rejected_on_warm_batch(batch_case):
    case = batch_case
    seed(case)
    case.backend._generation_writer = None
    with pytest.raises(GenerationUnavailableError, match="single-attempt"):
        case.backend.get_generations(SCOPES)
    assert case.client.reads == case.client.calls == []


def test_base_exception_is_not_swallowed(batch_case):
    case = batch_case
    case.client.error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        case.backend.get_generations(SCOPES)


class BatchSharedBackend(SharedBackend):
    def __init__(self):
        super().__init__()
        self.batches = []

    def get_generations(self, scopes):
        self.batches.append(scopes)
        # Reverse insertion order: result keys must retain canonical scope order.
        return {scope: self.get_generation(scope) for scope in reversed(scopes)}


@pytest.fixture
def batched():
    backend = BatchSharedBackend()
    return backend, QuerySetCache(backend=backend), QuerySetCache(backend=backend)


@pytest.mark.parametrize("entry", ["get", "get_or_set"])
def test_hit_keeps_two_separate_generation_reads(batched, entry):
    backend, reader, _ = batched
    reader.get_or_set(QueryStub())
    backend.batches.clear()
    query = QueryStub(2)
    assert getattr(reader, entry)(query) == [1]
    assert query.executions == 0
    assert backend.batches == [tuple(sorted(SCOPES[:2]))] * 2


@pytest.mark.parametrize("entry", ["get", "get_or_set"])
def test_rotation_between_batch_reads_rejects_stale_hit(batched, monkeypatch, entry):
    backend, reader, writer = batched
    reader.get_or_set(QueryStub())
    original_get = backend.get

    def invalidate_after_read(key):
        value = original_get(key)
        writer.invalidate_model("record")
        return value

    monkeypatch.setattr(backend, "get", invalidate_after_read)
    query = QueryStub(2)
    assert getattr(reader, entry)(query) == (None if entry == "get" else [2])
    assert query.executions == (0 if entry == "get" else 1)


@pytest.mark.parametrize("phase", ["after-sql", "inside-set"])
def test_late_fill_retains_original_generation(batched, phase):
    backend, reader, writer = batched
    query = QueryStub(
        after_read=lambda: writer.invalidate_model("record") if phase == "after-sql" else None
    )
    old_key = reader._generate_key(query)[0]
    if phase == "inside-set":
        backend.before_write = lambda: writer.invalidate_model("record")
    assert reader.get_or_set(query) == [1]
    assert backend.get(old_key) == ([1] if phase == "inside-set" else None)
    assert reader.get(QueryStub()) is None
    assert reader.get_or_set(QueryStub(2)) == [2]


@pytest.mark.parametrize("phase", ["initial", "verification"])
@pytest.mark.parametrize("failure", ["missing", "invalid", "exception"])
def test_invalid_batch_bypasses_without_publishing(batched, monkeypatch, phase, failure):
    backend, reader, _ = batched
    reader.get_or_set(QueryStub())
    previous_writes = list(backend.writes)
    original = backend.get_generations
    calls = 0

    def broken(scopes):
        nonlocal calls
        calls += 1
        values = original(scopes)
        if phase == "verification" and calls == 1:
            return values
        if failure == "missing":
            values.pop(scopes[-1])
        elif failure == "invalid":
            values[scopes[-1]] = "bad"
        else:
            raise OSError("batch unavailable")
        return values

    monkeypatch.setattr(backend, "get_generations", broken)
    query = QueryStub(2)
    assert reader.get_or_set(query) == [2]
    assert query.executions == 1
    assert backend.writes == previous_writes


def test_pending_rotation_must_finish_before_batch_read(batched, monkeypatch):
    backend, reader, writer = batched
    reader.get_or_set(QueryStub())
    backend.fail_rotate = True
    writer.invalidate_model("record")
    calls = Mock(wraps=backend.get_generations)
    monkeypatch.setattr(backend, "get_generations", calls)
    assert writer.get(QueryStub()) is None
    calls.assert_not_called()
    backend.fail_rotate = False
    assert writer.get(QueryStub()) is None
    assert not writer._state.pending_generations
    assert calls.call_count == 1
    assert reader.get(QueryStub()) is None


def test_batch_and_scalar_backends_generate_identical_result_keys(batched):
    backend, reader, _ = batched
    query = QueryStub()
    new_key = reader._generate_key(query)
    legacy = SharedBackend()
    legacy.tokens.update(backend.tokens)
    scalar = QuerySetCache(backend=legacy)
    assert scalar._generate_key(query) == new_key

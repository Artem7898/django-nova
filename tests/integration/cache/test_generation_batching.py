"""Real Redis/Memcached command counts and generation failure boundaries."""

from contextlib import contextmanager

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from nova.cache.backends.redis import RedisCacheBackend
from nova.cache.generation import GenerationUnavailableError, generation_key, generation_scope
from tests.cache.test_related_invalidation import relations as relations
from tests.integration.cache.test_queryset_remote_backends import remote_cache as remote_cache

pytestmark = pytest.mark.integration


def make_query(case, kind):
    query = case.Article.objects.filter(pk=case.article.pk)
    if kind == "select_related":
        return query.select_related("author__profile")
    if kind == "prefetch_related":
        return query.prefetch_related("tags")
    return query


def values(rows, kind):
    result = [(row.pk, row.title) for row in rows]
    if kind == "select_related":
        return result, [(row.author.name, row.author.profile.label) for row in rows]
    if kind == "prefetch_related":
        return result, [[tag.name for tag in row.tags.all()] for row in rows]
    return result


@contextmanager
def count_reads(backend, monkeypatch):
    """Observe commands at the native client boundary, below backend methods."""
    client = backend._client
    commands = []
    with monkeypatch.context() as patcher:
        if isinstance(backend, RedisCacheBackend):
            original = client.execute_command

            def execute(name, *keys, **kwargs):
                commands.append((str(name).upper(), tuple(keys)))
                return original(name, *keys, **kwargs)

            patcher.setattr(client, "execute_command", execute)
        else:
            original = client._fetch_cmd

            def fetch(name, keys, *args, **kwargs):
                keys = tuple(keys)
                commands.append((name.decode("ascii").upper(), keys))
                return original(name, keys, *args, **kwargs)

            patcher.setattr(client, "_fetch_cmd", fetch)
        yield commands


@pytest.mark.parametrize("kind", ["plain", "select_related", "prefetch_related"])
@pytest.mark.parametrize("entry", ["get", "get_or_set"])
def test_warm_hit_requires_three_read_commands(relations, remote_cache, monkeypatch, kind, entry):
    reader = remote_cache.cache
    query = make_query(relations, kind)
    expected = values(reader.get_or_set(query), kind)
    backend = reader._state.backend.backend
    with count_reads(backend, monkeypatch) as commands, CaptureQueriesContext(connection) as sql:
        assert values(getattr(reader, entry)(query), kind) == expected
    assert len(sql) == 0
    assert len(commands) == 3, commands
    generation_command = "MGET" if isinstance(backend, RedisCacheBackend) else "GET"
    assert [name for name, _ in commands] == [generation_command, "GET", generation_command]
    assert len(commands[0][1]) == (2 if kind == "plain" else 6)
    assert len(commands[1][1]) == 1
    assert commands[0] == commands[2], "Both phases must check the same complete scope vector"


@pytest.mark.parametrize("phase", ["initial", "verification"])
def test_failed_batch_read_bypasses_cache_without_publication(
    relations, remote_cache, monkeypatch, phase
):
    reader = remote_cache.cache
    query = make_query(relations, "plain")
    reader.get_or_set(query)
    key = reader._generate_key(query)[0]
    backend = reader._state.backend.backend
    # Deliberately bypass signals: the failure must never serve this old value.
    relations.Article.objects.filter(pk=relations.article.pk).update(title="new")
    method = "mget" if isinstance(backend, RedisCacheBackend) else "get_many"
    original = getattr(backend._client, method)
    calls = 0

    def fail(keys):
        nonlocal calls
        calls += 1
        if phase == "initial" or calls >= 2:
            raise OSError("injected batch transport failure")
        return original(keys)

    with monkeypatch.context() as patcher:
        patcher.setattr(backend._client, method, fail)
        with CaptureQueriesContext(connection) as sql:
            result = reader.get_or_set(query)
        assert [row.title for row in result] == ["new"]
        assert len(sql) == 1
    assert [row.title for row in backend.get(key)] == ["article"], "Failed batch must not publish"


@pytest.mark.parametrize("alias", ["default", "*"])
def test_evicted_dependency_metadata_cannot_reuse_an_old_result(relations, remote_cache, alias):
    reader = remote_cache.cache
    query = make_query(relations, "select_related")
    assert reader.get_or_set(query)[0].author.name == "author-old"
    key = reader._generate_key(query)[0]
    backend = reader._state.backend.backend
    scope = generation_scope("author", alias)
    metadata_key = generation_key(scope)
    raw_key = (
        backend._make_key(metadata_key) if isinstance(backend, RedisCacheBackend) else metadata_key
    )
    old_token = backend._client.get(raw_key)
    assert old_token is not None
    relations.Author.objects.filter(pk=relations.author.pk).update(name="new")
    assert backend._client.delete(raw_key)
    with CaptureQueriesContext(connection) as sql:
        assert reader.get_or_set(query)[0].author.name == "new"
    assert len(sql) == 1
    assert backend._client.get(raw_key) != old_token
    assert backend.get(key)[0].author.name == "author-old"
    with CaptureQueriesContext(connection) as sql:
        assert reader.get_or_set(query)[0].author.name == "new"
    assert len(sql) == 0


def test_corrupt_dependency_token_is_not_overwritten(relations, remote_cache):
    reader = remote_cache.cache
    query = make_query(relations, "select_related")
    reader.get_or_set(query)
    backend = reader._state.backend.backend
    key = generation_key(generation_scope("author", "default"))
    if isinstance(backend, RedisCacheBackend):
        key = backend._make_key(key)
        backend._client.set(key, b"bad-token")
    else:
        backend._client.set(key, b"bad-token", noreply=False)
    relations.Author.objects.filter(pk=relations.author.pk).update(name="new")
    with CaptureQueriesContext(connection) as sql:
        assert reader.get_or_set(query)[0].author.name == "new"
    assert len(sql) == 1
    assert backend._client.get(key) == b"bad-token"


@pytest.mark.parametrize("remote_cache", ["redis"], indirect=True)
def test_redis_mget_wrong_type_is_not_misread_as_missing(relations, remote_cache):
    reader = remote_cache.cache
    query = make_query(relations, "plain")
    reader.get_or_set(query)
    backend = reader._state.backend.backend
    scope = generation_scope("article", "default")
    key = backend._make_key(generation_key(scope))
    backend._client.delete(key)
    backend._client.hset(key, mapping={"corrupt": "hash"})
    with pytest.raises(GenerationUnavailableError):
        backend.get_generations((scope,))
    assert backend._client.hgetall(key) == {b"corrupt": b"hash"}
    with CaptureQueriesContext(connection) as sql:
        assert len(reader.get_or_set(query)) == 1
    assert len(sql) == 1

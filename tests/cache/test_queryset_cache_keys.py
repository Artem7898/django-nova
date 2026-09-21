"""Portable keys and correct query identity across cache backends."""

import os
import subprocess
import sys
from decimal import Decimal
from types import SimpleNamespace

import pytest

from nova.cache.backends.memory import MemoryCacheBackend
from nova.cache.queryset_cache import QuerySetCache


class QueryStub:
    def __init__(
        self, *, db="default", model="record", sql="SELECT value WHERE id=%s", params=(1,)
    ):
        self.db = db
        self.model = SimpleNamespace(_meta=SimpleNamespace(app_label="tests", model_name=model))
        self.query = SimpleNamespace(sql_with_params=lambda: (sql, params))

    def __iter__(self):
        return iter([1])


class SameReprParameter:
    """An adapter-style object whose repr deliberately hides the real value."""

    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return "PreparedValue(...)"


def key_for(query):
    return QuerySetCache(backend=MemoryCacheBackend())._generate_key(query)[0]


@pytest.mark.parametrize(
    "value", ["has spaces", "缓存\n\t🙂", "x" * 10_000], ids=["spaces", "unicode", "long"]
)
def test_keys_are_bounded_ascii_without_sql_or_parameter_text(value):
    key = key_for(QueryStub(params=(value,)))
    assert len(key.encode("ascii")) <= 200  # Leave room for a client namespace.
    assert all(33 <= ord(char) <= 126 for char in key)
    assert "SELECT" not in key
    assert value not in key


@pytest.mark.parametrize("db", ["副本 空格", "alias" * 200], ids=["unicode", "long"])
def test_unusual_database_aliases_do_not_break_transport_keys(db):
    key = key_for(QueryStub(db=db, model="model" * 100))
    assert len(key.encode("ascii")) <= 200
    assert all(33 <= ord(char) <= 126 for char in key)


@pytest.mark.parametrize(
    "change",
    [
        {"params": (2,)},
        {"params": ("1",)},
        {"params": (Decimal("1"),)},
        {"sql": "SELECT value WHERE id=%s ORDER BY value"},
        {"db": "replica"},
        {"model": "neighbor"},
    ],
)
def test_query_identity_separates_results(change):
    assert key_for(QueryStub()) != key_for(QueryStub(**change))


def test_equal_queries_have_equal_keys():
    assert key_for(QueryStub(params=("缓存", 3))) == key_for(QueryStub(params=("缓存", 3)))


def test_parameter_repr_is_not_used_as_the_entire_identity():
    first, second = SameReprParameter(1), SameReprParameter(2)
    assert repr(first) == repr(second)
    assert key_for(QueryStub(params=(first,))) != key_for(QueryStub(params=(second,)))


def test_memoryview_parameters_are_supported():
    first = key_for(QueryStub(params=(memoryview(b"first"),)))
    assert first == key_for(QueryStub(params=(memoryview(b"first"),)))
    assert first != key_for(QueryStub(params=(memoryview(b"second"),)))


def test_keys_are_stable_across_python_hash_seeds():
    code = """
from tests.cache.test_queryset_cache_keys import QueryStub, key_for
print(key_for(QueryStub(params=("Nova", 12))))
"""
    outputs = []
    for seed in ("1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=os.pathsep.join(sys.path))
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        outputs.append(result.stdout.strip())
    assert outputs[0] == outputs[1]


def test_sql_compiler_receives_queryset_database_alias():
    calls = []
    query = QueryStub(db="analytics")

    def get_compiler(*, using):
        calls.append(using)
        return SimpleNamespace(as_sql=lambda: ("SELECT backend_specific_sql", ()))

    query.query = SimpleNamespace(get_compiler=get_compiler)
    key_for(query)
    assert calls == ["analytics"]


@pytest.mark.parametrize("reader", ["get", "get_or_set"])
def test_remote_hit_is_registered_for_local_invalidation(reader):
    backend = MemoryCacheBackend()
    writer = QuerySetCache(backend=backend)
    reader_cache = QuerySetCache(backend=backend)  # Independent local index.
    writer.get_or_set(QueryStub())
    assert getattr(reader_cache, reader)(QueryStub()) == [1]
    assert reader_cache.invalidate_model("tests.record") == 1
    assert writer.get(QueryStub()) is None


def test_invalidation_still_respects_database_alias():
    cache = QuerySetCache(backend=MemoryCacheBackend())
    default, replica = QueryStub(), QueryStub(db="replica")
    cache.get_or_set(default)
    cache.get_or_set(replica)
    assert cache.invalidate_model("tests.record", db="default") == 1
    assert cache.get(default) is None
    assert cache.get(replica) == [1]
    assert cache.invalidate_model("tests.record", db="*") == 1
    assert cache.get(replica) is None


def test_values_and_values_list_do_not_share_a_key():
    from tests.models import CachedItem

    base = CachedItem.objects.all()
    keys = {
        key_for(base.values("value")),
        key_for(base.values_list("value")),
        key_for(base.values_list("value", flat=True)),
        key_for(base.values_list("value", named=True)),
    }
    assert len(keys) == 4


@pytest.mark.parametrize("empty_filter", [False, True], ids=["none", "empty-in"])
def test_statically_empty_queryset_can_be_cached_without_sql(
    empty_filter, transactional_db, django_assert_num_queries
):
    from tests.models import CachedItem

    cache = QuerySetCache(backend=MemoryCacheBackend())
    query = CachedItem.objects.filter(pk__in=[]) if empty_filter else CachedItem.objects.none()
    with django_assert_num_queries(0):
        assert cache.get_or_set(query) == []
        assert cache.get(query) == []

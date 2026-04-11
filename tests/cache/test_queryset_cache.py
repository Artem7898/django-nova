
"""
Tests for QuerySet caching layer.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import models

from nova.cache.queryset_cache import QuerySetCache
from nova.typing.models import NovaModel, NovaConfig


class CachedItem(NovaModel):
    name = models.CharField(max_length=100)
    value = models.IntegerField()

    class Meta:
        app_label = "tests"

    _nova_config = NovaConfig(cache_enabled=True)


class TestQuerySetCache:
    """Test suite for QuerySetCache."""

    def test_cache_miss_returns_none(self) -> None:
        cache: QuerySetCache[CachedItem] = QuerySetCache(maxsize=100, ttl=60)
        qs = CachedItem.objects.all()
        assert cache.get(qs) is None

    def test_get_or_set_executes_query_on_miss(self, db: None) -> None:
        cache: QuerySetCache[CachedItem] = QuerySetCache(maxsize=100, ttl=60)

        CachedItem.objects.create(name="item1", value=10)

        result = cache.get_or_set(CachedItem.objects.all())
        assert len(result) == 1
        assert result[0].name == "item1"

    def test_get_or_set_returns_cached_on_hit(self, db: None) -> None:
        cache: QuerySetCache[CachedItem] = QuerySetCache(maxsize=100, ttl=60)

        CachedItem.objects.create(name="item1", value=10)

        # First call — cache miss
        first = cache.get_or_set(CachedItem.objects.all())
        # Second call — cache hit
        second = cache.get_or_set(CachedItem.objects.all())

        assert first[0].pk == second[0].pk

    def test_invalidate_model_clears_entries(self, db: None) -> None:
        cache: QuerySetCache[CachedItem] = QuerySetCache(maxsize=100, ttl=60)

        CachedItem.objects.create(name="item1", value=10)
        cache.get_or_set(CachedItem.objects.all())

        assert cache.stats["currsize"] == 1
        cache.invalidate_model("cacheditem")
        assert cache.stats["currsize"] == 0

    def test_clear_empties_cache(self, db: None) -> None:
        cache: QuerySetCache[CachedItem] = QuerySetCache(maxsize=100, ttl=60)

        CachedItem.objects.create(name="item1", value=10)
        cache.get_or_set(CachedItem.objects.all())

        cache.clear()
        assert cache.stats["currsize"] == 0

    def test_stats_returns_correct_values(self) -> None:
        cache: QuerySetCache[CachedItem] = QuerySetCache(maxsize=500, ttl=120)
        stats = cache.stats

        assert stats["maxsize"] == 500
        assert stats["ttl"] == 120
        assert stats["currsize"] == 0

    def test_different_queries_have_different_keys(self, db: None) -> None:
        cache: QuerySetCache[CachedItem] = QuerySetCache(maxsize=100, ttl=60)

        CachedItem.objects.create(name="item1", value=10)
        CachedItem.objects.create(name="item2", value=20)

        result1 = cache.get_or_set(CachedItem.objects.filter(value=10))
        result2 = cache.get_or_set(CachedItem.objects.filter(value=20))

        assert cache.stats["currsize"] == 2
        assert result1[0].value == 10
        assert result2[0].value == 20
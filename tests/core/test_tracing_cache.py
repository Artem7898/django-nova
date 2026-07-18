"""Tests for Cache layer instrumentation."""
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest
from nova.cache.queryset_cache import QuerySetCache
from nova.cache.backends.memory import MemoryCacheBackend

@pytest.fixture
def mock_span():
    span = MagicMock()

    span.__enter__ = MagicMock(return_value=span)
    span.__exit__ = MagicMock(return_value=False)
    return span

@patch("nova.cache.queryset_cache.nova_span")
def test_get_or_set_miss_lifecycle(mock_nova_span, mock_span, db):
    mock_nova_span.return_value = mock_span

    from tests.models import CachedItem
    CachedItem.objects.create(name="test", value=1)

    cache = QuerySetCache(backend=MemoryCacheBackend())
    qs = CachedItem.objects.all()

    cache.get_or_set(qs)


    mock_nova_span.assert_any_call("nova.cache.lookup", model="cacheditem")
    mock_nova_span.assert_any_call("nova.cache.store", model="cacheditem")


    mock_span.set_attribute.assert_any_call("cache.outcome", "miss")

@patch("nova.cache.queryset_cache.nova_span")
def test_invalidate_lifecycle(mock_nova_span, mock_span, db):
    mock_nova_span.return_value = mock_span
    from tests.models import CachedItem

    cache = QuerySetCache(backend=MemoryCacheBackend())
    cache.invalidate_model("cacheditem")


    mock_nova_span.assert_called_once_with("nova.cache.invalidate", model="cacheditem")
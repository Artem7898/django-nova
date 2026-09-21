"""The result ownership contract also holds with real Redis and Memcached."""

import pytest

from tests.cache.test_queryset_result_isolation import (
    check_json_isolated,
    check_related_isolated,
    check_rows_isolated,
)
from tests.cache.test_queryset_result_isolation import rows_query as rows_query
from tests.cache.test_related_invalidation import relations as relations
from tests.integration.cache.test_queryset_remote_backends import remote_cache as remote_cache

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("entry", ["fill", "get", "get_or_set"])
@pytest.mark.parametrize("mutation", ["clear", "field"])
def test_remote_rows_are_isolated(remote_cache, rows_query, entry, mutation):
    check_rows_isolated(remote_cache.cache, rows_query, entry, mutation)


@pytest.mark.parametrize("entry", ["fill", "get"])
@pytest.mark.parametrize("kind", ["profile", "tag", "comment"])
def test_remote_related_objects_are_isolated(remote_cache, relations, entry, kind):
    check_related_isolated(remote_cache.cache, relations, entry, kind)


@pytest.mark.parametrize("shape", ["model", "dict", "tuple", "named", "flat"])
def test_remote_nested_json_is_isolated(remote_cache, rows_query, shape):
    check_json_isolated(remote_cache.cache, rows_query, "get", shape)

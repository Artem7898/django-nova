"""Related writes from a cache client which has never read the cached query."""

import pytest

from nova.cache.invalidation import connect_invalidation
from tests.cache import test_related_invalidation as contract
from tests.cache.test_related_invalidation import relations as relations
from tests.integration.cache.test_queryset_remote_backends import remote_cache as remote_cache


@pytest.fixture
def related_remote(relations, remote_cache):
    reader = remote_cache.cache
    writer = remote_cache.new_cache()
    connect_invalidation(relations.Article, cache=writer)
    assert not writer._state.key_models
    return reader, writer


@pytest.mark.parametrize("kind", ["author", "profile", "tag", "comment"])
@pytest.mark.parametrize("operation", ["save", "delete"])
@pytest.mark.parametrize("rollback", [False, True], ids=["commit", "rollback"])
def test_remote_related_write(relations, related_remote, kind, operation, rollback):
    contract.check_related_write(relations, *related_remote, kind, operation, rollback)


@pytest.mark.parametrize("operation", ["add", "remove", "clear"])
@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
@pytest.mark.parametrize("rollback", [False, True], ids=["commit", "rollback"])
def test_remote_m2m_change(relations, related_remote, operation, reverse, rollback):
    contract.check_m2m(relations, *related_remote, operation, reverse, rollback)

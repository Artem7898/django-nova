"""QuerySet reuse and transaction visibility with independently stored results."""

import pytest

from tests.cache import test_queryset_read_consistency as contract
from tests.integration.cache.test_queryset_remote_backends import remote_cache as remote_cache

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("operation", ["save", "delete"])
@pytest.mark.parametrize("shape", ["models", "flat"])
def test_remote_fill_does_not_republish_an_evaluated_queryset(remote_cache, operation, shape):
    contract.check_evaluated_queryset(remote_cache.cache, operation, shape)


@pytest.mark.parametrize("mode", ["atomic", "manual"])
@pytest.mark.parametrize("reader", ["get", "get_or_set"])
def test_remote_hit_does_not_hide_transaction_writes(remote_cache, mode, reader):
    contract.check_transaction_hit(remote_cache.cache, mode, reader)


@pytest.mark.parametrize("mode", ["atomic", "manual"])
def test_remote_transaction_miss_never_publishes_uncommitted_rows(remote_cache, mode):
    contract.check_transaction_miss(remote_cache.cache, mode)


def test_remote_autocommit_still_hits_cache(remote_cache):
    contract.check_autocommit_hit(remote_cache.cache)

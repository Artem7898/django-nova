"""Opt-in synchronous adapter, outside the normal testpaths."""

import pytest

pytestmark = pytest.mark.benchmark


def test_gc_preparation(request):
    from benchmarks.gc_preparation import execute

    execute(request.config)

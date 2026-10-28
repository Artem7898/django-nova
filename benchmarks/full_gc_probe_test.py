"""Explicit synchronous adapter; excluded from the project's normal testpaths."""

import pytest

pytestmark = pytest.mark.benchmark


def test_full_gc_probe(request):
    from benchmarks.full_gc_probe import execute

    execute(request.config)

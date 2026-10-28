"""Pytest-only adapter; never imported by the minimal inventory worker."""

import pytest

pytestmark = pytest.mark.benchmark


def test_gc_inventory(request):
    from benchmarks.gc_inventory import execute

    plugins = sorted(
        (dist.project_name, dist.version)
        for _, dist in request.config.pluginmanager.list_plugin_distinfo()
    )
    execute("pytest", plugins)

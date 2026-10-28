"""Pytest adapter for exactly the same fixed-payload worker as minimal Django."""

import pytest

pytestmark = pytest.mark.benchmark


def test_serializer_environment(request):
    from benchmarks.serializer_environment import execute

    plugins = sorted(
        (dist.project_name, dist.version)
        for _, dist in request.config.pluginmanager.list_plugin_distinfo()
    )
    execute("pytest", plugins)

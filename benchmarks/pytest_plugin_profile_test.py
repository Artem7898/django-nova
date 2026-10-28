"""Explicit adapter, collected only by the diagnostic launcher's command."""

import pytest

pytestmark = pytest.mark.benchmark


def test_pytest_plugin_profile(request):
    from benchmarks.pytest_plugin_profile import execute

    execute(request.config)

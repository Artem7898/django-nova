"""Same Django settings in both workers; only the test runner differs."""

import importlib
import os

_base = importlib.import_module(os.environ["NOVA_ENV_BASE_SETTINGS"])
for _name in dir(_base):
    if _name.isupper():
        globals()[_name] = getattr(_base, _name)
INSTALLED_APPS = list(_base.INSTALLED_APPS)
if "tests.cache.relations_app" not in INSTALLED_APPS:
    INSTALLED_APPS.append("tests.cache.relations_app")

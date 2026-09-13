"""Contracts for public exports and lazy package imports."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("public_module", "name", "source_module"),
    [
        ("nova", "get_default_cache", "nova.cache.queryset_cache"),
        ("nova.cache", "get_default_cache", "nova.cache.queryset_cache"),
        ("nova", "NovaManager", "nova.typing.managers"),
        ("nova.typing", "NovaManager", "nova.typing.managers"),
        ("nova", "TypedField", "nova.typing.fields"),
        ("nova.typing", "TypedField", "nova.typing.fields"),
        ("nova", "TypedQuerySet", "nova.typing.querysets"),
    ],
)
def test_public_export_matches_implementation(
    public_module: str,
    name: str,
    source_module: str,
) -> None:
    public = importlib.import_module(public_module)
    source = importlib.import_module(source_module)

    assert name in public.__all__
    assert getattr(public, name) is getattr(source, name)


@pytest.mark.parametrize(
    "module_name",
    ["nova", "nova.cache", "nova.typing"],
)
def test_unknown_export_raises_attribute_error(
    module_name: str,
) -> None:
    module = importlib.import_module(module_name)

    with pytest.raises(AttributeError):
        _ = module.__nova_missing_export__


def test_package_imports_do_not_load_django_or_pydantic() -> None:
    # A separate interpreter avoids pytest-django's initialized state.
    code = """
import importlib
import sys

class DependencyBlocker:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in {"django", "pydantic"}:
            raise AssertionError(
                f"Package import eagerly requested {fullname}"
            )
        return None

assert not any(
    name.split(".", 1)[0] in {"django", "pydantic"}
    for name in sys.modules
)

sys.meta_path.insert(0, DependencyBlocker())

for name in ("nova", "nova.cache", "nova.typing"):
    importlib.import_module(name)
"""

    env = os.environ.copy()
    env.pop("DJANGO_SETTINGS_MODULE", None)
    env["PYTHONPATH"] = os.pathsep.join(sys.path)

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr

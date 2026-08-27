from __future__ import annotations

import importlib

import pytest

import nova.tasks.backends as backends


def test_public_exports_are_stable() -> None:
    """The package exposes only the intentional public backend API."""
    assert backends.__all__ == [
        "AsyncioBackend",
        "RedisBackend",
        "TaskBackend",
    ]


def test_task_backend_is_resolved_lazily() -> None:
    """TaskBackend is exposed through the package-level compatibility boundary."""
    protocol = importlib.import_module("nova.tasks.backends.protocol")

    assert backends.TaskBackend is protocol.TaskBackend


def test_asyncio_backend_is_resolved_lazily() -> None:
    """AsyncioBackend resolves to the canonical implementation."""
    module = importlib.import_module(
        "nova.tasks.backends.asyncio_backend",
    )

    assert backends.AsyncioBackend is module.AsyncioBackend


def test_redis_backend_is_resolved_lazily() -> None:
    """RedisBackend resolves to the canonical implementation."""
    module = importlib.import_module(
        "nova.tasks.backends.redis_backend",
    )

    assert backends.RedisBackend is module.RedisBackend


def test_asyncio_legacy_alias_points_to_canonical_backend() -> None:
    """The historical AsyncioTaskBackend name remains compatible."""
    assert backends.AsyncioTaskBackend is backends.AsyncioBackend


def test_redis_legacy_alias_points_to_canonical_backend() -> None:
    """The historical RedisTaskBackend name remains compatible."""
    assert backends.RedisTaskBackend is backends.RedisBackend


@pytest.mark.parametrize(
    "name",
    [
        "DoesNotExist",
        "UnknownBackend",
        "TaskBackendFactory",
    ],
)
def test_unknown_attribute_raises_attribute_error(name: str) -> None:
    """Unknown package attributes fail with a normal AttributeError."""
    with pytest.raises(AttributeError, match="has no attribute"):
        getattr(backends, name)


def test_legacy_aliases_are_not_part_of_public_exports() -> None:
    """
    Legacy aliases remain import-compatible but are not advertised as
    part of the canonical public API.
    """
    assert "AsyncioTaskBackend" not in backends.__all__
    assert "RedisTaskBackend" not in backends.__all__


def test_package_does_not_eagerly_import_backend_classes() -> None:
    """
    Importing nova.tasks.backends itself should not eagerly import the
    concrete backend implementations.

    The package acts as a lazy compatibility boundary.
    """
    source = importlib.import_module("nova.tasks.backends")

    assert hasattr(source, "__getattr__")
    assert callable(source.__getattr__)


def test_package_import_is_lazy() -> None:
    """Importing the package does not import concrete backend modules."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import nova.tasks.backends; "
                "assert 'nova.tasks.backends.asyncio_backend' not in sys.modules; "
                "assert 'nova.tasks.backends.redis_backend' not in sys.modules; "
                "assert 'nova.tasks.backends.protocol' not in sys.modules"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0


def test_resolved_objects_are_classes() -> None:
    """All canonical backend exports resolve to classes."""
    assert isinstance(backends.TaskBackend, type)
    assert isinstance(backends.AsyncioBackend, type)
    assert isinstance(backends.RedisBackend, type)


def test_legacy_aliases_are_exact_identity_aliases() -> None:
    """
    Compatibility aliases must not create subclasses or wrappers.

    They must point directly at the canonical implementation.
    """
    assert backends.AsyncioTaskBackend is backends.AsyncioBackend
    assert backends.RedisTaskBackend is backends.RedisBackend

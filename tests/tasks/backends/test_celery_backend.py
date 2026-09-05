"""Tests for the Celery task backend stub.

The Celery backend is intentionally not implemented yet.

These tests therefore verify the architectural boundary of the stub:
- the module remains import-safe;
- the public class exists;
- the constructor exposes the expected API;
- construction fails explicitly with TaskBackendError;
- the failure communicates that Celery support is not implemented.

The tests must not pretend that a Celery execution backend already exists.
"""

from __future__ import annotations

import inspect

import pytest

from nova.tasks.backends.celery_backend import CeleryBackend
from nova.tasks.exceptions import TaskBackendError


def test_celery_backend_is_importable() -> None:
    """The optional Celery integration module must remain import-safe."""
    assert CeleryBackend is not None


def test_celery_backend_is_a_class() -> None:
    """CeleryBackend must expose a concrete public class."""
    assert inspect.isclass(CeleryBackend)


def test_celery_backend_constructor_exposes_app_name() -> None:
    """The public constructor must expose the application name."""
    signature = inspect.signature(CeleryBackend)

    app_name = signature.parameters.get("app_name")

    assert app_name is not None
    assert app_name.default == "nova"


def test_celery_backend_constructor_accepts_extra_options() -> None:
    """The constructor must preserve its extensible kwargs boundary."""
    signature = inspect.signature(CeleryBackend)

    kwargs = signature.parameters.get("kwargs")

    assert kwargs is not None
    assert kwargs.kind is inspect.Parameter.VAR_KEYWORD


def test_celery_backend_is_explicitly_unimplemented() -> None:
    """Construction must fail explicitly while the backend is a stub."""
    with pytest.raises(TaskBackendError):
        CeleryBackend()


def test_celery_backend_raises_nova_backend_error() -> None:
    """The stub must expose Nova's domain-specific backend exception."""
    with pytest.raises(TaskBackendError) as exc_info:
        CeleryBackend()

    assert isinstance(exc_info.value, TaskBackendError)


def test_celery_backend_error_explains_current_backend_status() -> None:
    """The failure message must clearly identify the implementation state."""
    with pytest.raises(TaskBackendError, match="not yet implemented"):
        CeleryBackend()


def test_celery_backend_error_recommends_asyncio_backend() -> None:
    """The current fallback must remain discoverable to callers."""
    with pytest.raises(TaskBackendError, match="asyncio"):
        CeleryBackend()


@pytest.mark.parametrize(
    ("app_name", "kwargs"),
    [
        ("nova", {}),
        ("production", {}),
        ("test", {"broker_url": "memory://"}),
        ("custom", {"result_backend": "redis://localhost"}),
    ],
)
def test_celery_backend_always_fails_explicitly_for_configuration(
    app_name: str,
    kwargs: dict[str, object],
) -> None:
    """Configuration must not accidentally turn the stub into partial behavior."""
    with pytest.raises(TaskBackendError, match="not yet implemented"):
        CeleryBackend(app_name=app_name, **kwargs)

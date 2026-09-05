"""Tests for the Redis task backend stub.

The Redis backend is intentionally not implemented yet.

These tests verify the architectural boundary of the current stub:
- the module is import-safe;
- the public class exists;
- the constructor exposes the expected configuration API;
- construction fails explicitly with TaskBackendError;
- configuration does not produce partial or accidental behavior.

The tests must not require a running Redis server.
"""

from __future__ import annotations

import inspect

import pytest

from nova.tasks.backends.redis_backend import RedisBackend
from nova.tasks.exceptions import TaskBackendError


def test_redis_backend_is_importable() -> None:
    """The Redis integration module must remain import-safe."""
    assert RedisBackend is not None


def test_redis_backend_is_a_class() -> None:
    """RedisBackend must expose a concrete public class."""
    assert inspect.isclass(RedisBackend)


def test_redis_backend_constructor_exposes_url() -> None:
    """The public constructor must expose the Redis connection URL."""
    signature = inspect.signature(RedisBackend)

    url = signature.parameters.get("url")

    assert url is not None
    assert url.default == "redis://localhost:6379/0"


def test_redis_backend_constructor_accepts_extra_options() -> None:
    """The constructor must preserve its extensible configuration boundary."""
    signature = inspect.signature(RedisBackend)

    kwargs = signature.parameters.get("kwargs")

    assert kwargs is not None
    assert kwargs.kind is inspect.Parameter.VAR_KEYWORD


def test_redis_backend_is_explicitly_unimplemented() -> None:
    """Construction must fail explicitly while the backend is a stub."""
    with pytest.raises(TaskBackendError):
        RedisBackend()


def test_redis_backend_raises_nova_backend_error() -> None:
    """The stub must expose Nova's domain-specific backend exception."""
    with pytest.raises(TaskBackendError) as exc_info:
        RedisBackend()

    assert isinstance(exc_info.value, TaskBackendError)


def test_redis_backend_error_explains_current_backend_status() -> None:
    """The failure message must clearly identify the implementation state."""
    with pytest.raises(TaskBackendError, match="not yet implemented"):
        RedisBackend()


def test_redis_backend_error_recommends_asyncio_backend() -> None:
    """The current fallback must remain discoverable to callers."""
    with pytest.raises(TaskBackendError, match="asyncio"):
        RedisBackend()


@pytest.mark.parametrize(
    ("url", "kwargs"),
    [
        ("redis://localhost:6379/0", {}),
        ("redis://localhost:6379/1", {}),
        ("redis://redis:6379/0", {"socket_timeout": 1.0}),
        ("redis://example.invalid:6379/0", {"decode_responses": True}),
    ],
)
def test_redis_backend_always_fails_explicitly_for_configuration(
    url: str,
    kwargs: dict[str, object],
) -> None:
    """Configuration must not accidentally create partial backend behavior."""
    with pytest.raises(TaskBackendError, match="not yet implemented"):
        RedisBackend(url=url, **kwargs)

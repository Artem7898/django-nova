"""
Memcached cache backend.

Requires pymemcache for production usage.
Supports dependency injection for contract testing.
"""

from __future__ import annotations

import importlib
import math
import time
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any, Protocol, cast

from ..generation import (
    GenerationUnavailableError,
    GenerationWriter,
    generation_key,
    new_generation,
    validate_generation,
)
from .protocol import TTL, CacheBackend
from .serializers import CacheSerializer, PickleSerializer


class _MemcachedClient(Protocol):
    """
    Minimal structural contract for the memcached client.

    Django Nova only depends on the operations it actually uses.
    """

    def get(self, key: str) -> bytes | None: ...

    def set(self, key: str, value: bytes, expire: int = 0) -> Any: ...

    def delete(self, key: str) -> Any: ...

    def flush_all(self) -> Any: ...


_MemcachedClientFactory = Callable[..., _MemcachedClient]

_memcached_available: bool = False
PyMemcacheClient: _MemcachedClientFactory | None = None

try:
    _pymemcache_base = importlib.import_module("pymemcache.client.base")
except ImportError:
    _pymemcache_base = None

if _pymemcache_base is not None:
    _client_cls: object = getattr(_pymemcache_base, "Client", None)

    if _client_cls is not None:
        PyMemcacheClient = cast(_MemcachedClientFactory, _client_cls)
        _memcached_available = True


_MISSING: object = object()
_ENVELOPE_VERSION = "nova:memcached:v2"
_MAX_RELATIVE_TTL = 60 * 60 * 24 * 30


def _parse_server(server: str) -> tuple[str, int]:
    """Convert host:port (or [IPv6]:port) to a pymemcache TCP address."""
    host, separator, port_text = server.rpartition(":")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    elif ":" in host:
        raise ValueError("IPv6 Memcached addresses must use [host]:port")

    if (
        not separator
        or not host
        or any(char.isspace() or char in "[]/" for char in host)
        or not port_text.isascii()
        or not port_text.isdecimal()
    ):
        raise ValueError("Memcached server must be host:port or [IPv6]:port")

    port = int(port_text)
    if not 1 <= port <= 65535:
        raise ValueError("Memcached port must be between 1 and 65535")
    return host, port


class MemcachedCacheBackend(CacheBackend):
    """
    Memcached cache backend.

    Supports dependency injection:

        MemcachedCacheBackend(client=fake_client)

    Injected clients must acknowledge deletes; configure pymemcache clients
    with default_noreply=False for accurate delete/delete_many results.

    Sub-second TTL is enforced at application level because memcached
    expire granularity is one second. Versioned payloads use Unix deadlines,
    requiring synchronized wall clocks across cache clients and the server.
    Legacy payloads with monotonic deadlines are treated as cache misses.
    """

    _serializer: CacheSerializer

    def __init__(
        self,
        server: str = "127.0.0.1:11211",
        *,
        client: _MemcachedClient | None = None,
        generation_writer: GenerationWriter | None = None,
    ) -> None:
        if client is None:
            if not _memcached_available or PyMemcacheClient is None:
                raise ImportError("pymemcache is required for MemcachedCacheBackend")

            client = PyMemcacheClient(
                _parse_server(server),
                default_noreply=False,
            )

        self._client: _MemcachedClient = client
        self._serializer = PickleSerializer()
        self._generation_writer = generation_writer

    @property
    def returns_detached_values(self) -> bool:
        """Native pickle reads own their result; custom implementations opt in."""
        return type(self) is MemcachedCacheBackend and type(self._serializer) is PickleSerializer

    @property
    def stores_detached_values(self) -> bool:
        """Native set serializes its envelope before invoking the client."""
        return type(self) is MemcachedCacheBackend and type(self._serializer) is PickleSerializer

    def get_generation(self, scope: str) -> str:
        """Initialize metadata with acknowledged ADD, never a blind SET."""
        key = generation_key(scope)
        client: Any = self._client
        try:
            writer = self._get_generation_writer()
            for _ in range(3):
                raw: object = client.get(key)
                if raw is not None:
                    return validate_generation(raw)
                writer.write(key, new_generation().encode("ascii"), only_if_absent=True)
            raise GenerationUnavailableError("Shared cache generation disappeared repeatedly")
        except Exception as exc:
            if isinstance(exc, GenerationUnavailableError):
                raise
            raise GenerationUnavailableError("Cannot read Memcached cache generation") from exc

    def get_generations(self, scopes: tuple[str, ...]) -> dict[str, str]:
        """Read warm metadata in one get_many; safely initialize missing keys."""
        scopes = tuple(dict.fromkeys(scopes))
        if not scopes:
            return {}
        try:
            self._get_generation_writer()
            read_many: Any = getattr(self._client, "get_many", None)
            if not callable(read_many):
                return {scope: self.get_generation(scope) for scope in scopes}
            keys = [generation_key(scope) for scope in scopes]
            raw: object = read_many(keys)
            if not isinstance(raw, Mapping):
                raise GenerationUnavailableError("Invalid Memcached generation batch response")
            values = cast("Mapping[str, object]", raw)
            tokens = {
                scope: validate_generation(values[key])
                for scope, key in zip(scopes, keys, strict=True)
                if key in values
            }
            for scope, key in zip(scopes, keys, strict=True):
                if key not in values:
                    tokens[scope] = self.get_generation(scope)
            return tokens
        except Exception as exc:
            if isinstance(exc, GenerationUnavailableError):
                raise
            raise GenerationUnavailableError("Cannot read Memcached cache generations") from exc

    def rotate_generation(self, scope: str) -> str:
        """Require acknowledgement even when an injected client uses noreply."""
        token = new_generation()
        try:
            if (
                self._get_generation_writer().write(
                    generation_key(scope), token.encode("ascii"), only_if_absent=False
                )
                is not True
            ):
                raise GenerationUnavailableError(
                    "Memcached did not acknowledge generation rotation"
                )
        except Exception as exc:
            if isinstance(exc, GenerationUnavailableError):
                raise
            raise GenerationUnavailableError("Cannot rotate Memcached cache generation") from exc
        return token

    def _get_generation_writer(self) -> GenerationWriter:
        if self._generation_writer is None:
            from ..generation_transport import memcached_generation_writer

            self._generation_writer = memcached_generation_writer(self._client)
        return self._generation_writer

    #
    # Internal helpers
    #

    def _ttl_seconds(self, ttl: TTL) -> float | None:
        if ttl is None:
            return None

        if isinstance(ttl, timedelta):
            return ttl.total_seconds()

        return float(ttl)

    def _memcached_expire(self, ttl: TTL) -> int:
        seconds = self._ttl_seconds(ttl)

        if seconds is None:
            return 0

        if seconds <= 0:
            return 0

        if seconds > _MAX_RELATIVE_TTL:
            return math.ceil(time.time() + seconds)
        return max(1, math.ceil(seconds))

    def _pack(self, value: Any, ttl: TTL) -> bytes:
        seconds = self._ttl_seconds(ttl)

        expires_at: float | None = None if seconds is None else time.time() + seconds

        return self._serializer.dumps((_ENVELOPE_VERSION, expires_at, value))

    def _unpack(self, raw: bytes | None) -> Any:
        if raw is None:
            return _MISSING

        loaded: object = self._serializer.loads(raw)

        if not isinstance(loaded, tuple):
            return _MISSING

        envelope = cast("tuple[object, ...]", loaded)

        # Legacy entries without TTL are safe to reuse. Legacy deadlines
        # used a machine-local monotonic clock and cannot be translated.
        if len(envelope) == 2:
            return envelope[1] if envelope[0] is None else _MISSING

        if len(envelope) != 3 or envelope[0] != _ENVELOPE_VERSION:
            return _MISSING

        expires_at: object = envelope[1]
        value: object = envelope[2]

        if expires_at is not None:
            if isinstance(expires_at, bool) or not isinstance(expires_at, int | float):
                return _MISSING

            if not math.isfinite(expires_at) or time.time() >= expires_at:
                return _MISSING

        return value

    #
    # Core operations
    #

    def get(self, key: str, default: Any | None = None) -> Any | None:
        raw = self._client.get(key)
        value = self._unpack(raw)

        if value is _MISSING:
            return default

        return value

    def set(self, key: str, value: Any, *, ttl: TTL = None) -> None:
        seconds = self._ttl_seconds(ttl)
        if seconds is not None and seconds <= 0:
            self._client.delete(key)
            return

        payload = self._pack(value, ttl)
        expire = self._memcached_expire(ttl)

        self._client.set(key, payload, expire=expire)

    def delete(self, key: str) -> bool:
        return bool(self._client.delete(key))

    def clear(self) -> None:
        self._client.flush_all()

    #
    # Bulk operations
    #

    def get_many(self, keys: list[str]) -> Mapping[str, Any]:
        return {key: self.get(key) for key in keys}

    def set_many(self, values: Mapping[str, Any], *, ttl: TTL = None) -> None:
        for key, value in values.items():
            self.set(key, value, ttl=ttl)

    def delete_many(self, keys: list[str]) -> int:
        count = 0

        for key in keys:
            if self.delete(key):
                count += 1

        return count

    #
    # Introspection
    #

    @property
    def backend_name(self) -> str:
        return "memcached"

    @property
    def supports_ttl(self) -> bool:
        return True

    @property
    def supports_atomic_increment(self) -> bool:
        return True

    @property
    def supports_pattern_delete(self) -> bool:
        return False

    def size(self) -> int:
        return -1

    def stats(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "currsize": self.size(),
            "maxsize": None,
            "ttl": None,
        }

"""Intelligent QuerySet caching with automatic invalidation and OTEL Instrumentation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar, cast

from django.db.models import Model

from nova.cache.backends.memory import MemoryCacheBackend
from nova.cache.backends.protocol import CacheBackend
from nova.core.exceptions import NovaCacheError
from nova.core.observability import get_logger
from nova.core.tracing import nova_span

if TYPE_CHECKING:
    from django.db.models import QuerySet

P = ParamSpec("P")
R = TypeVar("R")
ModelT = TypeVar("ModelT", bound=Model)

logger = get_logger(__name__)


class QuerySetCache[ModelT: Model]:
    """Type-safe cache for Django QuerySet results with full tracing lifecycle."""

    def __init__(
        self,
        *,
        backend: CacheBackend | None = None,
        ttl: int = 60,
        key_prefix: str = "nova_qs",
    ) -> None:
        self._backend = backend or MemoryCacheBackend()
        self._ttl = ttl
        self._key_prefix = key_prefix
        self._model_keys: dict[str, set[str]] = {}

    def _generate_key(self, queryset: QuerySet[ModelT]) -> tuple[str, str]:
        try:
            meta: Any = getattr(queryset.model, "_meta", None)
            model_name = cast(str, getattr(meta, "model_name", "unknown_model"))

            query_obj: Any = getattr(queryset, "query", None)
            compiler = query_obj.get_compiler(using=queryset.db)

            sql, params = cast("tuple[str, Any]", compiler.as_sql())
            safe_params = json.dumps(params, sort_keys=True, default=str)
            raw_key = f"{self._key_prefix}:{model_name}:{sql}:{safe_params}"
            return hashlib.sha256(raw_key.encode()).hexdigest(), model_name
        except Exception as exc:
            raise NovaCacheError(f"Failed to generate cache key: {exc}") from exc

    def get(self, queryset: QuerySet[ModelT]) -> list[ModelT] | None:
        """Return cached result or None with trace spans."""
        key, model_name = self._generate_key(queryset)

        with nova_span("nova.cache.get", model=model_name) as span:
            result = cast("list[ModelT] | None", self._backend.get(key))

            if result is not None:
                if span:
                    span.set_attribute("cache.outcome", "hit")
                with nova_span("nova.cache.hit", model=model_name):
                    pass
            else:
                if span:
                    span.set_attribute("cache.outcome", "miss")
                with nova_span("nova.cache.miss", model=model_name):
                    pass

            return result

    def get_or_set(self, queryset: QuerySet[ModelT]) -> list[ModelT]:
        """Return cached result or execute query, cache it, with tracing."""
        key, model_name = self._generate_key(queryset)

        with nova_span("nova.cache.lookup", model=model_name) as span:
            cached = cast("list[ModelT] | None", self._backend.get(key))
            if cached is not None:
                if span:
                    span.set_attribute("cache.outcome", "hit")
                return cached

            if span:
                span.set_attribute("cache.outcome", "miss")

        with nova_span("nova.cache.store", model=model_name) as span:
            result = list(queryset)
            self._backend.set(key, result, self._ttl)
            self._model_keys.setdefault(model_name, set()).add(key)
            if span:
                span.set_attribute("cache.ttl", self._ttl)

        return result

    def invalidate_model(self, model_name: str) -> int:
        """Invalidate cached queries with tracing."""
        with nova_span("nova.cache.invalidate", model=model_name) as span:
            keys_to_remove = self._model_keys.pop(model_name, set())

            for key in keys_to_remove:
                self._backend.delete(key)

            if keys_to_remove:
                logger.info("cache_invalidate", model=model_name, evicted_count=len(keys_to_remove))
                if span:
                    span.set_attribute("cache.evicted_count", len(keys_to_remove))

            return len(keys_to_remove)

    def clear(self) -> None:
        self._backend.clear()
        self._model_keys.clear()
        logger.info("queryset_cache_cleared")

    @property
    def stats(self) -> dict[str, Any]:
        # Cast removed: Protocol.stats() already returns dict[str, Any]
        backend_stats = self._backend.stats()
        backend_stats["tracked_models"] = len(self._model_keys)
        return backend_stats


_default_cache: QuerySetCache[Any] | None = None

def get_default_cache() -> QuerySetCache[Any]:
    global _default_cache
    if _default_cache is None:
        _default_cache = QuerySetCache(backend=MemoryCacheBackend(), ttl=120)
    return _default_cache

def cached_queryset(
    cache: QuerySetCache[Any] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    actual_cache = cache or get_default_cache()
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            result = func(*args, **kwargs)
            if hasattr(result, "model") and hasattr(result, "query"):
                return cast(R, actual_cache.get_or_set(cast("QuerySet[Any]", result)))
            return result
        return wrapper
    return decorator
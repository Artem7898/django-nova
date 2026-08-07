"""
Signal-driven QuerySet cache.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, cast

from django.db.models.query import QuerySet

from ..core.tracing import nova_span
from .backends.protocol import CacheBackend

logger = logging.getLogger(__name__)


def _rlock_factory() -> RLock:
    return RLock()


def _model_keys_factory() -> dict[str, set[str]]:
    return {}


def _key_models_factory() -> dict[str, set[str]]:
    return {}


@dataclass
class _QuerySetCacheState:
    """
    Shared internal state for QuerySetCache.

    This allows QuerySetCache[Model]() to use the default process-wide
    cache without forcing users to pass a backend manually.
    """

    backend: CacheBackend
    ttl: int
    lock: RLock = field(default_factory=_rlock_factory)
    model_keys: dict[str, set[str]] = field(default_factory=_model_keys_factory)
    key_models: dict[str, set[str]] = field(default_factory=_key_models_factory)


class QuerySetCache[T]:
    """
    Signal-driven QuerySet cache with model-level invalidation.
    """

    def __init__(
        self,
        backend: CacheBackend | None = None,
        *,
        ttl: int = 300,
        _state: _QuerySetCacheState | None = None,
    ) -> None:
        if _state is not None:
            self._state = _state
        elif backend is None:
            self._state = _get_default_state()
        else:
            self._state = _QuerySetCacheState(
                backend=backend,
                ttl=ttl,
            )

    #
    # Internal helpers
    #

    @staticmethod
    def _short_model_name(model_name: str) -> str:
        """
        Return short lowercased model name.

        Examples:
        - "cacheditem" -> "cacheditem"
        - "CachedItem" -> "cacheditem"
        - "tests.CachedItem" -> "cacheditem"
        - "tests.cacheditem" -> "cacheditem"
        """
        normalized = model_name.strip().lower()
        if not normalized:
            return ""

        if "." in normalized:
            return normalized.rpartition(".")[2]

        return normalized

    @staticmethod
    def _normalize_model_names(model_name: str) -> frozenset[str]:
        """
        Normalize invalidation target.

        Supports:
        - "cacheditem"
        - "CachedItem"
        - "tests.CachedItem"
        - "tests.cacheditem"
        """
        normalized = model_name.strip().lower()
        if not normalized:
            return frozenset()

        names = {normalized}

        if "." in normalized:
            short_name = normalized.rpartition(".")[2]
            if short_name:
                names.add(short_name)

        return frozenset(names)

    def _model_identifiers(self, model: Any) -> tuple[str, str, frozenset[str]]:
        """
        Return:
        - full model name: app_label.model_name
        - short model name: model_name
        - all names used for invalidation indexing
        """
        meta: Any = getattr(model, "_meta", None)
        if meta is None:
            raise ValueError(
                "Cannot generate cache key for QuerySet without a model"
            )

        app_label = str(getattr(meta, "app_label", "") or "")
        short_name = str(getattr(meta, "model_name", "") or "").lower()

        if not short_name:
            short_name = str(getattr(model, "__name__", "")).lower()

        full_name = f"{app_label}.{short_name}" if app_label else short_name
        names = {short_name, full_name}

        return full_name, short_name, frozenset(names)

    def _generate_key(
        self,
        queryset: QuerySet[T],
    ) -> tuple[str, str, frozenset[str]]:
        """
        Return:
        - cache key
        - short model name for tracing
        - model names for invalidation index
        """
        model: Any = getattr(queryset, "model", None)
        if model is None:
            raise ValueError(
                "Cannot generate cache key for QuerySet without a model"
            )

        db = str(getattr(queryset, "db", "default") or "default")
        full_name, short_name, names = self._model_identifiers(model)

        query: Any = getattr(queryset, "query", None)
        if query is None:
            raise ValueError("QuerySet has no query attribute")

        sql_with_params: Any = getattr(query, "sql_with_params", None)

        sql: str
        params: Any

        if callable(sql_with_params):
            sql, params = cast("tuple[str, Any]", sql_with_params())
        else:
            sql, params = str(query), ()

        key = f"nova:qs:{db}:{full_name}:{sql}:{params!r}"

        return key, short_name, names

    def _register_key(self, key: str, names: frozenset[str]) -> None:
        state = self._state

        with state.lock:
            for name in names:
                state.model_keys.setdefault(name, set()).add(key)

            state.key_models.setdefault(key, set()).update(names)

    #
    # Public API
    #

    def get(self, queryset: QuerySet[T]) -> list[T] | None:
        """
        Return cached result or None on miss.
        """
        state = self._state
        key, short_name, _ = self._generate_key(queryset)

        with nova_span("nova.cache.lookup", model=short_name) as span:
            cached: Any = state.backend.get(key)

            if cached is not None:
                if span:
                    span.set_attribute("cache.outcome", "hit")
                return cast("list[T]", cached)

            if span:
                span.set_attribute("cache.outcome", "miss")

            return None

    def get_or_set(self, queryset: QuerySet[T]) -> list[T]:
        """
        Return cached result or execute query, cache it, with tracing.
        """
        state = self._state
        key, short_name, names = self._generate_key(queryset)

        with nova_span("nova.cache.lookup", model=short_name) as span:
            cached: Any = state.backend.get(key)

            if cached is not None:
                if span:
                    span.set_attribute("cache.outcome", "hit")
                return cast("list[T]", cached)

            if span:
                span.set_attribute("cache.outcome", "miss")

        with nova_span("nova.cache.store", model=short_name) as span:
            result: list[Any] = list(queryset)

            state.backend.set(key, result, ttl=state.ttl)
            self._register_key(key, names)

            if span:
                span.set_attribute("cache.rows", len(result))

            return cast("list[T]", result)

    def invalidate_model(self, model_name: str, db: str = "default") -> int:
        """
        Invalidate all cached QuerySets for a model.

        Returns number of deleted cache entries.
        """
        short_name = self._short_model_name(model_name)

        with nova_span("nova.cache.invalidate", model=short_name) as span:
            names = self._normalize_model_names(model_name)
            if not names:
                if span:
                    span.set_attribute("cache.invalidated", 0)
                return 0

            match_all_dbs = db == "*"
            prefix = "" if match_all_dbs else f"nova:qs:{db}:"

            state = self._state

            with state.lock:
                keys: set[str] = set()

                for name in names:
                    bucket = state.model_keys.get(name)
                    if not bucket:
                        continue

                    matched = {
                        key
                        for key in bucket
                        if match_all_dbs or key.startswith(prefix)
                    }

                    if not matched:
                        continue

                    keys.update(matched)
                    bucket.difference_update(matched)

                    if not bucket:
                        del state.model_keys[name]

                if not keys:
                    if span:
                        span.set_attribute("cache.invalidated", 0)
                    return 0

                for key in keys:
                    related_names = state.key_models.pop(key, set())

                    for related_name in related_names:
                        related_bucket = state.model_keys.get(related_name)

                        if related_bucket is not None:
                            related_bucket.discard(key)

                            if not related_bucket:
                                del state.model_keys[related_name]

            deleted = 0

            for key in keys:
                try:
                    if state.backend.delete(key):
                        deleted += 1
                except Exception:
                    logger.warning(
                        "Failed to delete cache key %s during model invalidation",
                        key,
                        exc_info=True,
                    )

            if deleted:
                logger.debug(
                    "Invalidated %d cache entries for %s",
                    deleted,
                    model_name,
                )

            if span:
                span.set_attribute("cache.invalidated", deleted)

            return deleted

    def invalidate(self, model_name: str, db: str = "default") -> int:
        """
        Backward-compatible alias.
        """
        return self.invalidate_model(model_name, db)

    def clear(self) -> None:
        """
        Clear the whole cache.
        """
        state = self._state

        with state.lock:
            try:
                state.backend.clear()
            except Exception:
                logger.warning(
                    "Failed to clear cache backend",
                    exc_info=True,
                )

            state.model_keys.clear()
            state.key_models.clear()

    @property
    def stats(self) -> dict[str, Any]:
        return self.get_stats()

    def get_stats(self) -> dict[str, Any]:
        """
        Aggregate stats from the underlying backend.
        """
        state = self._state
        stats_func: Any = getattr(state.backend, "stats", None)

        if callable(stats_func):
            return cast("dict[str, Any]", stats_func())

        backend_name = getattr(state.backend, "backend_name", "unknown")

        return {"backend": backend_name}


#
# Default cache state
#

_default_state: _QuerySetCacheState | None = None
_default_cache: QuerySetCache[Any] | None = None


def _get_default_state() -> _QuerySetCacheState:
    """
    Return shared default cache state.

    Lazily creates a memory-backed cache so importing this module
    never requires external services.
    """
    global _default_state

    if _default_state is None:
        from .backends.memory import MemoryCacheBackend

        _default_state = _QuerySetCacheState(
            backend=MemoryCacheBackend(),
            ttl=300,
        )

    return _default_state


def get_default_cache() -> QuerySetCache[Any]:
    """
    Return the process-wide default QuerySetCache.
    """
    global _default_cache

    if _default_cache is None:
        _default_cache = QuerySetCache(_state=_get_default_state())

    return _default_cache


def reset_default_cache() -> None:
    """
    Reset the default cache. Useful for tests.
    """
    global _default_cache
    global _default_state

    _default_cache = None
    _default_state = None
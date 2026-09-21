"""
Signal-driven QuerySet cache.
"""

from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
from threading import RLock
from typing import Any, cast

from django.core.exceptions import EmptyResultSet
from django.db import connections
from django.db import models as django_models
from django.db.models.query import QuerySet

from ..core.tracing import nova_span
from .backends.protocol import CacheBackend
from .dependencies import UncacheableQueryError, query_dependencies
from .generation import (
    BatchGenerationBackend,
    GenerationBackend,
    GenerationUnavailableError,
    generation_scope,
    validate_generation,
)
from .read_contract import DetachedReadBackend
from .result_snapshot import snapshot_rows
from .write_contract import DetachedWriteBackend

logger = logging.getLogger(__name__)


class _KeyPickler(pickle.Pickler):
    """Serialize full query parameters, including Django binary parameters."""

    def reducer_override(self, obj: object) -> Any:
        if isinstance(obj, memoryview):
            return bytes, (obj.tobytes(),)
        return NotImplemented


def _query_digest(parts: tuple[Any, ...]) -> str:
    buffer = BytesIO()
    _KeyPickler(buffer, protocol=5).dump(parts)
    return sha256(buffer.getvalue()).hexdigest()


def _database_prefix(db: str) -> str:
    return f"nova:qs:v4:{sha256(db.encode('utf-8')).hexdigest()}:"


@dataclass(frozen=True, slots=True)
class _KeySnapshot:
    key: str
    short_name: str
    names: frozenset[str]
    generations: tuple[tuple[str, str], ...]


def _rlock_factory() -> RLock:
    return RLock()


def _model_keys_factory() -> dict[str, set[str]]:
    return {}


def _key_models_factory() -> dict[str, set[str]]:
    return {}


def _invalidated_keys_factory() -> set[str]:
    return set()


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
    generation: int = 0
    invalidated_keys: set[str] = field(default_factory=_invalidated_keys_factory)
    clear_failed: bool = False
    pending_generations: set[str] = field(default_factory=_invalidated_keys_factory)


class QuerySetCache[T: django_models.Model]:
    """
    Signal-driven QuerySet cache with model-level invalidation.

    Backends implementing GenerationBackend coordinate readers using shared
    random tokens. Result keys retain the token captured before SQL, so late
    fills cannot republish data into a newer generation. Other backends keep
    process-local fencing. Unrelated local invalidations may skip a fill.

    Failed deletions remain indexed and unreadable until a successful retry
    or fresh query fill. A failed clear bypasses cache reads and writes until
    clear() succeeds. A failed shared rotation is logged and retried on the
    next local access; remote notification during a partition is not guaranteed.

    Reads inside atomic blocks or with autocommit disabled bypass the cache.
    Misses execute a fresh QuerySet clone on the database chosen for this call,
    preserving the caller's evaluated results and unsaved Python objects.

    Standard SQL joins and string prefetch paths include related models and
    M2M through models in their index and shared generation vector. Prefetch
    plans are part of result identity. Untracked plans bypass caching.

    Cache entries and returned rows own independent object graphs. Loaded
    relations survive the snapshot; deferred fields stay deferred. Backends
    explicitly guaranteeing independent storage may serialize SQL results
    directly. Other backends receive a defensive snapshot; snapshot failures
    return the SQL result without publication.
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
            raise ValueError("Cannot generate cache key for QuerySet without a model")

        app_label = str(getattr(meta, "app_label", "") or "")
        short_name = str(getattr(meta, "model_name", "") or "").lower()

        if not short_name:
            short_name = str(getattr(model, "__name__", "")).lower()

        full_name = f"{app_label}.{short_name}" if app_label else short_name
        names = {short_name, full_name}

        return full_name, short_name, frozenset(names)

    def _read_generations(self, scopes: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        state = self._state
        backend = state.backend
        if not isinstance(backend, GenerationBackend):
            return ()
        with state.lock:
            try:
                for scope in scopes:
                    if scope in state.pending_generations:
                        validate_generation(backend.rotate_generation(scope))
                        state.pending_generations.discard(scope)
                if isinstance(backend, BatchGenerationBackend):
                    tokens = backend.get_generations(scopes)
                    if set(tokens) != set(scopes):
                        raise GenerationUnavailableError("Incomplete shared generation snapshot")
                    return tuple((scope, validate_generation(tokens[scope])) for scope in scopes)
                return tuple(
                    (scope, validate_generation(backend.get_generation(scope))) for scope in scopes
                )
            except Exception as exc:
                raise GenerationUnavailableError(
                    "Shared generation unavailable; bypassing cache"
                ) from exc

    def _generations_match(self, snapshot: _KeySnapshot) -> bool:
        if not snapshot.generations:
            return True
        try:
            current = self._read_generations(tuple(scope for scope, _ in snapshot.generations))
        except GenerationUnavailableError:
            logger.warning("Shared generation unavailable; bypassing cache", exc_info=True)
            return False
        return current == snapshot.generations

    def _backend_get(self, snapshot: _KeySnapshot) -> list[Any] | None:
        try:
            cached = self._state.backend.get(snapshot.key)
        except Exception:
            if not snapshot.generations:
                raise
            logger.warning("Shared cache read failed; treating as a miss", exc_info=True)
            return None
        if cached is None:
            return None
        try:
            if not isinstance(cached, list):
                raise TypeError("A cached QuerySet result must be a list")
            backend = self._state.backend
            if isinstance(backend, DetachedReadBackend) and backend.returns_detached_values is True:
                return cast("list[Any]", cached)
            return snapshot_rows(cached)
        except Exception:
            logger.warning("Cache result snapshot failed; treating as a miss", exc_info=True)
            return None

    def _backend_set(self, snapshot: _KeySnapshot, result: list[Any]) -> bool:
        backend = self._state.backend
        try:
            stores_detached = (
                isinstance(backend, DetachedWriteBackend) and backend.stores_detached_values is True
            )
        except Exception:
            logger.warning(
                "Cache write ownership unavailable; using a defensive snapshot",
                exc_info=True,
            )
            stores_detached = False
        if stores_detached:
            detached = result
        else:
            try:
                detached = snapshot_rows(result)
            except Exception:
                logger.warning(
                    "Cache result snapshot failed; returning database result without publication",
                    exc_info=True,
                )
                return False
        try:
            backend.set(snapshot.key, detached, ttl=self._state.ttl)
        except Exception:
            if not snapshot.generations:
                raise
            logger.warning("Shared cache write failed; returning database result", exc_info=True)
            return False
        return True

    def _key_snapshot(
        self,
        queryset: QuerySet[T],
    ) -> _KeySnapshot:
        """
        Return:
        - cache key
        - short model name for tracing
        - model names for invalidation index
        """
        model: Any = getattr(queryset, "model", None)
        if model is None:
            raise ValueError("Cannot generate cache key for QuerySet without a model")

        db = str(getattr(queryset, "db", "default") or "default")
        full_name, short_name, names = self._model_identifiers(model)

        query: Any = getattr(queryset, "query", None)
        if query is None:
            raise ValueError("QuerySet has no query attribute")

        get_compiler: Any = getattr(query, "get_compiler", None)
        sql_with_params: Any = getattr(query, "sql_with_params", None)

        sql: str
        params: Any

        try:
            if callable(get_compiler):
                compiler: Any = get_compiler(using=db)
                sql, params = cast("tuple[str, Any]", compiler.as_sql())
            elif callable(sql_with_params):
                sql, params = cast("tuple[str, Any]", sql_with_params())
            else:
                sql, params = str(query), ()
        except EmptyResultSet:
            sql, params = "", ()

        iterable: Any = getattr(queryset, "_iterable_class", None)
        result_shape = (
            str(getattr(iterable, "__module__", "")),
            str(getattr(iterable, "__qualname__", "")),
        )
        # Hash full parameter values, not repr() (adapters may truncate repr).
        # The versioned key is 140 ASCII bytes before a backend's own prefix.
        dependencies = query_dependencies(queryset)
        scopes = {generation_scope(short_name, "*"), generation_scope(short_name, db)}
        prefetches: tuple[str, ...] = ()
        if dependencies is not None:
            dependency_names = set(names)
            prefetches = dependencies.prefetches
            for dependency in dependencies.models:
                _, dependency_short, identifiers = self._model_identifiers(dependency)
                dependency_names.update(identifiers)
                scopes.add(generation_scope(dependency_short, "*"))
                scopes.add(generation_scope(dependency_short, db))
            names = frozenset(dependency_names)
        generations = self._read_generations(tuple(sorted(scopes)))
        digest = _query_digest((full_name, sql, params, result_shape, prefetches, generations))
        key = f"{_database_prefix(db)}{digest}"

        return _KeySnapshot(key, short_name, names, generations)

    def _generate_key(self, queryset: QuerySet[T]) -> tuple[str, str, frozenset[str]]:
        """Return the current key; generation-aware backends may perform I/O."""
        snapshot = self._key_snapshot(queryset)
        return snapshot.key, snapshot.short_name, snapshot.names

    @staticmethod
    def _prepare_queryset(queryset: object) -> QuerySet[T]:
        if isinstance(queryset, QuerySet):
            query = cast("QuerySet[T]", queryset)
            # using() clones without Django's result cache and pins routing
            # for the transaction check, cache key, and subsequent SQL.
            return query.using(query.db)
        # Lightweight query doubles have no Django result cache or connection.
        return cast("QuerySet[T]", queryset)

    @staticmethod
    def _transactional_query(queryset: object) -> bool:
        # Cached rows cannot implement transaction visibility. This applies to
        # local and shared backends, before any metadata or result access.
        if isinstance(queryset, QuerySet):
            query = cast("QuerySet[Any]", queryset)
            db = connections[query.db]
            return db.in_atomic_block or not db.get_autocommit()
        return False

    def _register_key(self, key: str, names: frozenset[str]) -> None:
        state = self._state

        with state.lock:
            for name in names:
                state.model_keys.setdefault(name, set()).add(key)

            state.key_models.setdefault(key, set()).update(names)

    def _unregister_key(self, key: str) -> None:
        """Forget an entry only after its removal has been acknowledged."""
        state = self._state
        with state.lock:
            for name in state.key_models.pop(key, set()):
                bucket = state.model_keys.get(name)
                if bucket is not None:
                    bucket.discard(key)
                    if not bucket:
                        del state.model_keys[name]
            state.invalidated_keys.discard(key)

    #
    # Public API
    #

    def get(self, queryset: QuerySet[T]) -> list[T] | None:
        """
        Return independent cached rows, or None on miss or within a transaction.
        """
        queryset = self._prepare_queryset(queryset)
        if self._transactional_query(queryset):
            return None
        state = self._state
        try:
            snapshot = self._key_snapshot(queryset)
        except UncacheableQueryError:
            return None
        except GenerationUnavailableError:
            logger.warning("Shared generation unavailable; bypassing cache", exc_info=True)
            return None
        key, short_name, names = snapshot.key, snapshot.short_name, snapshot.names

        with nova_span("nova.cache.lookup", model=short_name) as span:
            with state.lock:
                cached: Any = None
                if not state.clear_failed and key not in state.invalidated_keys:
                    cached = self._backend_get(snapshot)
                    if cached is not None and self._generations_match(snapshot):
                        self._register_key(key, names)
                    else:
                        cached = None

            if cached is not None:
                if span:
                    span.set_attribute("cache.outcome", "hit")
                return cast("list[T]", cached)

            if span:
                span.set_attribute("cache.outcome", "miss")

            return None

    def get_or_set(self, queryset: QuerySet[T]) -> list[T]:
        """
        Return independent cached rows or execute a fresh query.

        Transactional reads use their own connection without cache I/O.
        """
        queryset = self._prepare_queryset(queryset)
        if self._transactional_query(queryset):
            return list(queryset)
        state = self._state
        try:
            snapshot = self._key_snapshot(queryset)
        except UncacheableQueryError:
            return list(queryset)
        except GenerationUnavailableError:
            logger.warning("Shared generation unavailable; bypassing cache", exc_info=True)
            return list(queryset)
        key, short_name, names = snapshot.key, snapshot.short_name, snapshot.names

        with nova_span("nova.cache.lookup", model=short_name) as span:
            with state.lock:
                cached: Any = None
                if not state.clear_failed and key not in state.invalidated_keys:
                    cached = self._backend_get(snapshot)
                    if cached is not None and self._generations_match(snapshot):
                        self._register_key(key, names)
                    else:
                        cached = None
                generation = state.generation

            if cached is not None:
                if span:
                    span.set_attribute("cache.outcome", "hit")
                return cast("list[T]", cached)

            if span:
                span.set_attribute("cache.outcome", "miss")

        with nova_span("nova.cache.store", model=short_name) as span:
            result: list[Any] = list(queryset)

            # SQL runs outside the lock. An invalidation, even with no
            # registered keys, makes this in-flight fill unsafe to publish.
            with state.lock:
                # SET uses the original key even if another worker rotates
                # after the generation check and before the write completes.
                if (
                    generation == state.generation
                    and not state.clear_failed
                    and not self._transactional_query(queryset)
                    and self._generations_match(snapshot)
                    and self._backend_set(snapshot, result)
                ):
                    self._register_key(key, names)
                    state.invalidated_keys.discard(key)

            if span:
                span.set_attribute("cache.rows", len(result))

            return cast("list[T]", result)

    def invalidate_model(self, model_name: str, db: str = "default") -> int:
        """
        Invalidate all cached QuerySets for a model.

        Returns number of deleted cache entries. Failed keys remain blocked
        for reads and indexed for a later retry.
        """
        short_name = self._short_model_name(model_name)

        with nova_span("nova.cache.invalidate", model=short_name) as span:
            names = self._normalize_model_names(model_name)
            if not names:
                if span:
                    span.set_attribute("cache.invalidated", 0)
                return 0

            match_all_dbs = db == "*"
            prefix = "" if match_all_dbs else _database_prefix(db)

            state = self._state

            with state.lock:
                # A shared, process-local generation conservatively fences all
                # in-flight fills, including those not yet in the key index.
                state.generation += 1
                if isinstance(state.backend, GenerationBackend):
                    scope = generation_scope(short_name, db)
                    state.pending_generations.add(scope)
                    try:
                        validate_generation(state.backend.rotate_generation(scope))
                    except Exception:
                        logger.error(
                            "Shared cache generation rotation failed; remote readers may "
                            "retain stale data until retry or expiry",
                            exc_info=True,
                        )
                    else:
                        state.pending_generations.discard(scope)
                keys: set[str] = set()

                for name in names:
                    bucket = state.model_keys.get(name)
                    if not bucket:
                        continue

                    matched = {key for key in bucket if match_all_dbs or key.startswith(prefix)}

                    if not matched:
                        continue

                    keys.update(matched)

                if not keys:
                    if span:
                        span.set_attribute("cache.invalidated", 0)
                    return 0

                # A failed delete may leave the old value in the backend.
                # Keep both the read guard and the retry index in that case.
                state.invalidated_keys.update(keys)
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
                    else:
                        # False also confirms that the key is already absent.
                        self._unregister_key(key)

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
        Clear the whole cache. On failure, bypass it until clear() succeeds.
        """
        state = self._state

        with state.lock:
            state.generation += 1
            try:
                state.backend.clear()
            except Exception:
                state.clear_failed = True
                logger.warning(
                    "Failed to clear cache backend",
                    exc_info=True,
                )
                return

            state.model_keys.clear()
            state.key_models.clear()
            state.invalidated_keys.clear()
            state.pending_generations.clear()
            state.clear_failed = False

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

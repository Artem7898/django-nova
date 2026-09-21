"""
Event-driven cache invalidation using Django signals.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.db.models.signals import m2m_changed, post_delete, post_save

from .dependencies import related_model_graph
from .queryset_cache import QuerySetCache, get_default_cache

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

logger = logging.getLogger(__name__)

_CONNECTED_SIGNALS: set[tuple[type[Any], int]] = set()
_CONNECTED_M2M_SIGNALS: set[tuple[type[Any], int]] = set()


def _connect_signal(signal: Any, receiver: Callable[..., None], sender: type[Any]) -> None:
    signal.connect(receiver, sender=sender, weak=False)


def connect_invalidation(
    model_cls: type[NovaModel],
    cache: QuerySetCache[Any] | None = None,
) -> None:
    """
    Connect cache invalidation signals for a Nova model.

    Invalidate after the successful commit on the signal database.
    In autocommit mode, invalidation runs immediately. Rolled-back writes
    do not invalidate. Safe to call multiple times. Subscribe to the relation
    component at startup, including non-cache-enabled dependencies and M2M
    through models, so a writer need never have read a related cached query.
    """
    nova_config: Any = getattr(model_cls, "_nova_config", None)

    if not nova_config or not getattr(nova_config, "cache_enabled", False):
        return

    target_cache = cache or get_default_cache()
    if (model_cls, id(target_cache)) in _CONNECTED_SIGNALS:
        return

    related_models, through_models = related_model_graph(model_cls)

    def _invalidate(sender: Any, **kwargs: Any) -> None:
        meta: Any = getattr(sender, "_meta", None)
        if meta is None:
            return

        app_label = str(getattr(meta, "app_label", "") or "")
        model_name = str(getattr(meta, "model_name", "") or "")

        if not model_name:
            return

        full_name = f"{app_label}.{model_name}" if app_label else model_name
        db = str(kwargs.get("using", "default") or "default")

        def invalidate_after_commit() -> None:
            try:
                count: int = int(target_cache.invalidate_model(full_name, db))
            except Exception:
                logger.warning(
                    "Cache invalidation failed for %s",
                    full_name,
                    exc_info=True,
                )
                return

            if count > 0:
                logger.debug(
                    "Invalidated %d cache entries for %s",
                    count,
                    full_name,
                )

        transaction.on_commit(invalidate_after_commit, using=db)

    for dependency in related_models:
        connection_key = (dependency, id(target_cache))
        if connection_key not in _CONNECTED_SIGNALS:
            _connect_signal(post_save, _invalidate, dependency)
            _connect_signal(post_delete, _invalidate, dependency)
            _CONNECTED_SIGNALS.add(connection_key)

    def _invalidate_m2m(sender: Any, **kwargs: Any) -> None:
        if kwargs.get("action") in {"post_add", "post_remove", "post_clear"}:
            # Rotate the through-model scope, including reverse clear where
            # pk_set is None. Dependents include this scope before any fill.
            _invalidate(sender, **kwargs)

    for through in through_models:
        connection_key = (through, id(target_cache))
        if connection_key not in _CONNECTED_M2M_SIGNALS:
            _connect_signal(m2m_changed, _invalidate_m2m, through)
            _CONNECTED_M2M_SIGNALS.add(connection_key)

    logger.info(
        "cache_invalidation_connected",
        extra={
            "model": model_cls.__name__,
        },
    )

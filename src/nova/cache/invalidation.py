"""
Event-driven cache invalidation using Django signals.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.db.models.signals import post_delete, post_save

from .queryset_cache import QuerySetCache, get_default_cache

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

logger = logging.getLogger(__name__)

_CONNECTED_SIGNALS: set[tuple[type[Any], int]] = set()


def connect_invalidation(
    model_cls: type[NovaModel],
    cache: QuerySetCache[Any] | None = None,
) -> None:
    """
    Connect cache invalidation signals for a Nova model.

    Safe to call multiple times.
    """
    nova_config: Any = getattr(model_cls, "_nova_config", None)

    if not nova_config or not getattr(nova_config, "cache_enabled", False):
        return

    target_cache = cache or get_default_cache()

    connection_key = (model_cls, id(target_cache))
    if connection_key in _CONNECTED_SIGNALS:
        return

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

    post_save.connect(_invalidate, sender=model_cls, weak=False)  # type: ignore[arg-type]
    post_delete.connect(_invalidate, sender=model_cls, weak=False)  # type: ignore[arg-type]

    _CONNECTED_SIGNALS.add(connection_key)

    logger.info(
        "cache_invalidation_connected",
        extra={
            "model": model_cls.__name__,
        },
    )

"""
Event-driven cache invalidation using Django signals.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.db.models.signals import post_delete, post_save

from nova.cache.queryset_cache import QuerySetCache, get_default_cache

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

logger = logging.getLogger(__name__)


def connect_invalidation(
    model_cls: type[NovaModel],
    cache: QuerySetCache[Any] | None = None,
) -> None:
    """
    Connects signals for a specific NovaModel to invalidate cache on writes.

    Args:
        model_cls: The NovaModel class to monitor.
        cache: Specific cache instance to invalidate.
               If None, uses the global default cache.
    """
    if not model_cls._nova_config.cache_enabled:
        return

    # Используем переданный кеш или глобальный синглтон
    target_cache = cache or get_default_cache()

    def _invalidate(sender: Any, **kwargs: Any) -> None:
        model_name = sender._meta.model_name
        count = target_cache.invalidate_model(model_name)
        if count > 0:
            logger.debug("Invalidated %d queries for %s", count, model_name)

    post_save.connect(_invalidate, sender=model_cls, weak=False)
    post_delete.connect(_invalidate, sender=model_cls, weak=False)
    logger.info("Connected cache invalidation for %s", model_cls.__name__)
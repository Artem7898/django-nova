"""
Event-driven cache invalidation using Django signals.

Production requirements:
- idempotent signal registration
- multiple cache backends support
- safe autoreload behaviour
- zero duplicate handlers
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.db.models.signals import post_delete, post_save

from nova.cache.queryset_cache import QuerySetCache, get_default_cache

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

    Parameters
    ----------
    model_cls:
        Model class.

    cache:
        Optional cache backend instance.
    """

    if not model_cls._nova_config.cache_enabled:
        return

    target_cache = cache or get_default_cache()

    connection_key = (
        model_cls,
        id(target_cache),
    )

    if connection_key in _CONNECTED_SIGNALS:
        return

    def _invalidate(sender: Any, **kwargs: Any) -> None:
        model_name = sender._meta.model_name
        count = target_cache.invalidate_model(model_name)

        if count > 0:
            logger.debug(
                "Invalidated %d cache entries for %s",
                count,
                model_name,
            )

    post_save.connect(
        _invalidate,
        sender=model_cls,
        weak=False,
    )

    post_delete.connect(
        _invalidate,
        sender=model_cls,
        weak=False,
    )

    _CONNECTED_SIGNALS.add(connection_key)

    logger.info(
        "cache_invalidation_connected",
        extra={
            "model": model_cls.__name__,
        },
    )
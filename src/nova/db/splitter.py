"""Migration splitter for large data migrations.
Prevents OOM and transaction timeouts during RunPython.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from django.db import transaction
from django.db.migrations import RunPython

logger = logging.getLogger(__name__)


def chunked_migration(
    func: Callable[..., None],  # Changed from [[Any, Any], None] to allow **kwargs (like pks=...)
    batch_size: int = 1000,
) -> RunPython:
    """
    Wraps a RunPython migration to process data in chunks.

    The wrapped function will receive (apps, schema_editor) as usual,
    but it is expected to be written to handle a single batch.
    It must return a QuerySet for the next batch.
    """

    def chunked_wrapper(apps: Any, schema_editor: Any) -> None:
        Model = apps.get_model("app", "Model")
        qs = Model.objects.all().order_by("pk")

        while qs.exists():
            pks = list(qs.values_list("pk", flat=True)[:batch_size])
            with transaction.atomic():
                func(apps, schema_editor, pks=pks)
            qs = qs.filter(pk__gt=pks[-1])
            logger.info("Processed batch of %d, last pk: %d", batch_size, pks[-1])

    return RunPython(chunked_wrapper, reverse_code=RunPython.noop)
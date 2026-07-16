from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from django.db import models

from nova.typing.querysets import TypedQuerySet

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

ModelT = TypeVar("ModelT", bound="NovaModel")


class NovaManager(models.Manager[ModelT]):
    """
    Fully typed manager.

    Runtime behaviour remains identical to Django Manager,
    but static analyzers now understand actual model types.
    """

    _queryset_class = TypedQuerySet

    def get_queryset(self) -> TypedQuerySet[ModelT]:
        return TypedQuerySet(
            model=self.model,
            query=self.model._base_manager.all().query,
            using=self._db,
            hints=self._hints,
        )
"""In-process copies of ORM result graphs, including already-loaded relations."""

from __future__ import annotations

import pickle
from io import BytesIO
from typing import Any, cast

from django.db import models


class _ResultPickler(pickle.Pickler):
    def __init__(self, stream: BytesIO) -> None:
        super().__init__(stream, protocol=pickle.HIGHEST_PROTOCOL)
        self.model_types: dict[tuple[str, str], type[models.Model]] = {}

    def _remember_model(self, model: type[models.Model]) -> tuple[str, str]:
        meta: Any = model._meta
        identifier = (str(meta.app_label), str(meta.object_name))
        existing = self.model_types.setdefault(identifier, model)
        if existing is not model:
            raise TypeError("A snapshot contains different model classes with the same label")
        return identifier

    def reducer_override(self, value: object) -> Any:
        if isinstance(value, models.Model):
            self._remember_model(type(value))
        # Preserve Django/custom __reduce__ and __getstate__ behavior.
        return NotImplemented

    def persistent_id(self, value: object) -> tuple[str, str] | None:
        if isinstance(value, type) and issubclass(value, models.Model):
            # Prefetched QuerySets also reference their model class directly.
            return self._remember_model(value)
        return None


class _ResultUnpickler(pickle.Unpickler):
    def __init__(
        self, stream: BytesIO, model_types: dict[tuple[str, str], type[models.Model]]
    ) -> None:
        super().__init__(stream)
        self.model_types = model_types

    def persistent_load(self, identifier: Any) -> type[models.Model]:
        return self.model_types[identifier]

    def _restore_model(self, identifier: tuple[str, str]) -> models.Model:
        model = self.model_types[identifier]
        return model.__new__(model)

    def find_class(self, module: str, name: str) -> Any:
        if (module, name) == ("django.db.models.base", "model_unpickle"):
            # Resolve against this graph's classes, not the global registry.
            # This also supports Django's isolated/historical model registries.
            return self._restore_model
        return super().find_class(module, name)


def snapshot_rows(value: Any) -> list[Any]:
    """Detach trusted in-process rows; the temporary bytes never leave this call.

    Django QuerySet.__deepcopy__ drops _result_cache, including prefetched
    collections. Pickling preserves those rows, deferred fields and cycles.
    Custom serialization hooks keep their normal semantics and may raise.
    """
    if not isinstance(value, list):
        raise TypeError("A cached QuerySet result must be a list")
    stream = BytesIO()
    pickler = _ResultPickler(stream)
    pickler.dump(value)
    stream.seek(0)
    detached: Any = _ResultUnpickler(stream, pickler.model_types).load()
    if not isinstance(detached, list):
        raise TypeError("A QuerySet snapshot must preserve the result list")
    return cast("list[Any]", detached)

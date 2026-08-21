"""
Django type utilities for Nova.

This module is the single typing boundary between Django's dynamic
model metadata API and Nova's strictly typed core.

The runtime behavior of Django is preserved. Nova exposes only the
small contracts required by its internal subsystems.
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Protocol,
    TypeGuard,
    overload,
)

if TYPE_CHECKING:
    from django.db.models import Model

__all__ = [
    "ConcreteFieldProtocol",
    "get_concrete_fields",
    "get_model_field",
    "get_model_pk",
    "is_concrete_field",
    "is_generic_foreign_key",
    "safe_clean_field",
    "safe_get_attname",
]


class ConcreteFieldProtocol(Protocol):
    """
    Minimal contract for a concrete Django database field.

    Nova deliberately depends only on this small interface instead of
    exposing Django's generic Field type throughout the codebase.
    """

    name: str
    attname: str
    auto_created: bool
    many_to_many: bool
    is_relation: bool

    def clean(
        self,
        value: Any,
        model_instance: Model | None,
    ) -> Any:
        """Validate and normalize a field value."""
        ...


def _get_meta(model: type[Model]) -> Any:
    """
    Return Django's dynamic model metadata object.

    All access to Django's dynamic ``_meta`` API is isolated here.
    """
    return model._meta


@overload
def get_model_pk(
    model: type[Model],
    *,
    strict: Literal[True] = True,
) -> ConcreteFieldProtocol: ...


@overload
def get_model_pk(
    model: type[Model],
    *,
    strict: Literal[False],
) -> ConcreteFieldProtocol | None: ...


def get_model_pk(
    model: type[Model],
    *,
    strict: bool = True,
) -> ConcreteFieldProtocol | None:
    """
    Return the model primary-key field through Nova's typing boundary.

    With ``strict=True`` the function either returns a concrete field
    contract or raises AttributeError.

    With ``strict=False`` a missing primary key is represented by None.

    Django's ``_meta.pk`` is the authoritative source of truth for the
    model primary key. We therefore do not require the PK to pass the
    generic ``is_concrete_field()`` predicate.
    """
    pk = getattr(_get_meta(model), "pk", None)

    if pk is None:
        if strict:
            raise AttributeError(
                f"{model.__name__} has no primary key",
            )

        return None

    return pk


def get_model_field(
    model: type[Model],
    field_name: str,
) -> Any | None:
    """
    Return a Django model metadata field by name.

    Unlike ``get_concrete_fields()``, this function intentionally
    preserves Django's complete metadata contract. Query planning may
    need normal fields, forward relations, reverse relations and other
    metadata objects.

    Missing fields return None.
    """
    from django.core.exceptions import FieldDoesNotExist

    try:
        return _get_meta(model).get_field(field_name)
    except FieldDoesNotExist:
        return None


def get_concrete_fields(
    model: type[Model],
) -> list[tuple[str, ConcreteFieldProtocol]]:
    """
    Return concrete database fields required by Nova.

    GenericForeignKey, reverse relations, auto-created relations and
    many-to-many fields are excluded.
    """
    result: list[tuple[str, ConcreteFieldProtocol]] = []

    for model_field in _get_meta(model).get_fields():
        if not is_concrete_field(model_field):
            continue

        result.append(
            (model_field.name, model_field),
        )

    return result


def safe_get_attname(
    field: object,
) -> str | None:
    """
    Return a concrete field's database attribute name.

    Virtual relations and GenericForeignKey return None.
    """
    if not is_concrete_field(field):
        return None

    return field.attname


def safe_clean_field(
    field: object,
    value: Any,
    instance: Model,
) -> Any:
    """
    Safely invoke Django field validation.

    Non-concrete metadata objects are ignored because they do not expose
    Nova's concrete field validation contract.
    """
    if not is_concrete_field(field):
        return value

    return field.clean(value, instance)


def is_generic_foreign_key(
    obj: object,
) -> bool:
    """Return True when obj is a GenericForeignKey."""
    from django.contrib.contenttypes.fields import GenericForeignKey

    return isinstance(obj, GenericForeignKey)


def is_concrete_field(
    obj: object,
) -> TypeGuard[ConcreteFieldProtocol]:
    """
    Return whether an object represents a concrete database field.

    This is a real TypeGuard: after a successful check, callers may use
    the object as a ConcreteFieldProtocol without an additional cast.
    """
    from django.contrib.contenttypes.fields import GenericForeignKey
    from django.db.models import Field
    from django.db.models.fields.reverse_related import ForeignObjectRel

    if isinstance(obj, GenericForeignKey):
        return False

    if isinstance(obj, ForeignObjectRel):
        return False

    if not isinstance(obj, Field):
        return False

    return not (obj.auto_created or obj.many_to_many)

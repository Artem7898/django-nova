"""Tests for the relation graph analyzer."""

from __future__ import annotations

import collections.abc
from types import SimpleNamespace

from pydantic import BaseModel

from nova.query.relations import (
    _unwrap_core_type,
    find_deep_relations,
)


class AddressSchema(BaseModel):
    city: str


class CompanySchema(BaseModel):
    name: str
    address: AddressSchema | None = None


class UserSchema(BaseModel):
    name: str
    company: CompanySchema | None = None


class ItemSchema(BaseModel):
    name: str


class OrderSchema(BaseModel):
    item: ItemSchema | None = None
    items: list[ItemSchema] = []


class PrimitiveListSchema(BaseModel):
    values: list[str] = []


class RecursiveSchema(BaseModel):
    name: str
    children: list[RecursiveSchema] = []


RecursiveSchema.model_rebuild()


class FakeContainerSchema:
    model_fields = {
        "items": SimpleNamespace(
            annotation=collections.abc.Container[ItemSchema],
        )
    }


class FakeIterableSchema:
    model_fields = {
        "items": SimpleNamespace(
            annotation=collections.abc.Iterable[ItemSchema],
        )
    }


class TestUnwrapCoreType:
    """Tests for _unwrap_core_type."""

    def test_none_returns_none(self) -> None:
        assert _unwrap_core_type(None) is None

    def test_plain_model_returns_model(self) -> None:
        assert _unwrap_core_type(AddressSchema) is AddressSchema

    def test_optional_model_returns_model(self) -> None:
        annotation = AddressSchema | None

        assert _unwrap_core_type(annotation) is AddressSchema

    def test_union_with_non_model_and_model_returns_model(self) -> None:
        annotation = str | AddressSchema

        assert _unwrap_core_type(annotation) is AddressSchema

    def test_plain_non_model_returns_type(self) -> None:
        assert _unwrap_core_type(str) is str
        assert _unwrap_core_type(int) is int

    def test_generic_non_union_returns_none(self) -> None:
        assert _unwrap_core_type(list[AddressSchema]) is None


class TestFindDeepRelations:
    """Tests for find_deep_relations."""

    def test_plain_schema_has_no_relations(self) -> None:
        result = find_deep_relations(PrimitiveListSchema)

        assert result == {
            "select": [],
            "prefetch": [],
        }

    def test_nested_optional_relation_is_selected(self) -> None:
        result = find_deep_relations(UserSchema)

        assert result == {
            "select": [
                "company",
                "company__address",
            ],
            "prefetch": [],
        }

    def test_list_relation_is_prefetched(self) -> None:
        result = find_deep_relations(OrderSchema)

        assert result == {
            "select": ["item"],
            "prefetch": ["items"],
        }

    def test_container_relation_is_prefetched(self) -> None:
        result = find_deep_relations(FakeContainerSchema)

        assert result == {
            "select": [],
            "prefetch": ["items"],
        }

    def test_iterable_relation_is_prefetched(self) -> None:
        result = find_deep_relations(FakeIterableSchema)

        assert result == {
            "select": [],
            "prefetch": ["items"],
        }

    def test_path_prefix_is_applied(self) -> None:
        result = find_deep_relations(
            UserSchema,
            path_prefix="profile",
        )

        assert result == {
            "select": [
                "profile__company",
                "profile__company__address",
            ],
            "prefetch": [],
        }

    def test_exclude_by_field_name(self) -> None:
        result = find_deep_relations(
            UserSchema,
            exclude={"company"},
        )

        assert result == {
            "select": [],
            "prefetch": [],
        }

    def test_exclude_by_full_path(self) -> None:
        result = find_deep_relations(
            UserSchema,
            path_prefix="profile",
            exclude={"profile__company"},
        )

        assert result == {
            "select": [],
            "prefetch": [],
        }

    def test_exclude_nested_relation(self) -> None:
        result = find_deep_relations(
            UserSchema,
            exclude={"company__address"},
        )

        assert result == {
            "select": ["company"],
            "prefetch": [],
        }

    def test_self_reference_does_not_recurse(self) -> None:
        result = find_deep_relations(RecursiveSchema)

        assert result == {
            "select": [],
            "prefetch": ["children"],
        }

    def test_existing_visited_schema_is_skipped(self) -> None:
        result = find_deep_relations(
            UserSchema,
            visited={UserSchema},
        )

        assert result == {
            "select": [],
            "prefetch": [],
        }

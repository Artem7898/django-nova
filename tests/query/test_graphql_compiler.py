from __future__ import annotations

import pytest

pytest.importorskip("strawberry")

from nova.ecosystem import to_strawberry_type
from tests.models import ArticleWithRelations, GrantWithSecret, Lab
from tests.query.contracts import SchemaCompilerContract
from tests.query.fixtures import (
    grant_secret_expectation,
    lab_schema_expectation,
)


def _get_strawberry_fields(graphql_type) -> set[str]:
    if hasattr(graphql_type, "__strawberry_definition__"):
        return {f.name for f in graphql_type.__strawberry_definition__.fields}
    return set(getattr(graphql_type, "__annotations__", {}).keys())


@pytest.fixture()
def lab_contract() -> SchemaCompilerContract:
    # GraphQL type is a business projection: no PK, no DB artifacts
    return SchemaCompilerContract(lab_schema_expectation("GraphQL: Lab", include_pk=False))


@pytest.fixture()
def secret_contract() -> SchemaCompilerContract:
    return SchemaCompilerContract(
        grant_secret_expectation("GraphQL: GrantWithSecret", include_pk=False)
    )


def test_graphql_lab_projection(lab_contract: SchemaCompilerContract) -> None:
    graphql_type = to_strawberry_type(model_cls=Lab)
    lab_contract.check_projection(_get_strawberry_fields(graphql_type))


def test_graphql_lab_deterministic(lab_contract: SchemaCompilerContract) -> None:
    def factory():
        return _get_strawberry_fields(to_strawberry_type(model_cls=Lab))

    lab_contract.check_deterministic_projection(factory)


def test_graphql_lab_type_name() -> None:
    graphql_type = to_strawberry_type(model_cls=Lab)
    assert hasattr(graphql_type, "__name__")


def test_graphql_secret_field_hidden(secret_contract: SchemaCompilerContract) -> None:
    graphql_type = to_strawberry_type(model_cls=GrantWithSecret)
    fields = _get_strawberry_fields(graphql_type)
    secret_contract.check_projection(fields)
    assert "secret_note" not in fields


def test_graphql_nested_relation_compiled() -> None:
    graphql_type = to_strawberry_type(model_cls=ArticleWithRelations)
    fields = _get_strawberry_fields(graphql_type)
    assert {"title", "author", "tags"} <= fields


def test_graphql_schema_direct_pass() -> None:
    from tests.models import ArticleDeepSchema

    deep_type = to_strawberry_type(schema=ArticleDeepSchema)
    fields = _get_strawberry_fields(deep_type)
    assert {"title", "author", "tags"} <= fields


def test_graphql_model_without_schema_raises() -> None:
    from tests.models import Article

    with pytest.raises(ValueError, match="pydantic_schema"):
        to_strawberry_type(model_cls=Article)


def test_graphql_no_args_raises() -> None:
    with pytest.raises(ValueError, match="Either model_cls or schema"):
        to_strawberry_type()


def test_strawberry_available_flag() -> None:
    from nova.ecosystem.graphql import STRAWBERRY_AVAILABLE

    assert STRAWBERRY_AVAILABLE is True

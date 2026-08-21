"""Shared expectations for query contract tests."""

from __future__ import annotations

from typing import Any

from .contracts import (
    AsyncORMExpectation,
    InvalidCase,
    SchemaProjectionExpectation,
)


def lab_schema_expectation(
    target: str,
    *,
    include_pk: bool = True,
) -> SchemaProjectionExpectation:
    """Lab: name (str), budget (float >= 0)."""
    return SchemaProjectionExpectation(
        target=target,
        schema_fields=frozenset({"name", "budget"}),
        valid_payload={"name": "Quantum Lab", "budget": 50000.0},
        invalid_cases=(
            InvalidCase(
                payload={"name": "Quantum Lab", "budget": -1.0},
                reason="budget cannot be negative",
                expected_error_fields=frozenset({"budget"}),
            ),
        ),
        pk_fields=frozenset({"id"}) if include_pk else frozenset(),
    )


def grant_secret_expectation(
    target: str,
    *,
    include_pk: bool = True,
) -> SchemaProjectionExpectation:
    """GrantWithSecret: title, budget. secret_note is hidden."""
    return SchemaProjectionExpectation(
        target=target,
        schema_fields=frozenset({"title", "budget"}),
        valid_payload={"title": "Quantum Research Grant", "budget": 500000.0},
        invalid_cases=(
            InvalidCase(
                payload={"title": "X", "budget": 100.0},
                reason="title shorter than 5 characters",
                expected_error_fields=frozenset({"title"}),
            ),
        ),
        hidden_fields=frozenset({"secret_note"}),
        pk_fields=frozenset({"id"}) if include_pk else frozenset(),
    )


def article_relations_expectation(target: str) -> SchemaProjectionExpectation:
    """ArticleWithRelations: DRF exposes schema names (author, not author_id)."""
    return SchemaProjectionExpectation(
        target=target,
        schema_fields=frozenset({"title", "author", "tags"}),
        valid_payload={"title": "Deep Dive", "author": 1, "tags": [1, 2]},
        invalid_cases=(),
    )


def async_article_expectation(model: Any) -> AsyncORMExpectation:
    """AsyncArticle: setup creates the Author row author_id=1 references."""

    async def _create_author() -> None:
        from tests.models import Author

        exists = await Author.objects.filter(pk=1).aexists()
        if not exists:
            await Author.objects.acreate(id=1, name="Prerequisite Author")

    return AsyncORMExpectation(
        target="AsyncArticle async ORM contract",
        model=model,
        valid_payload={"title": "Async Article", "author_id": 1},
        invalid_cases=(),
        setup=_create_author,
    )

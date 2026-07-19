"""
Tests for the Schema-Driven Query Planner.

Architecture Note: Tests are divided into two categories:
1. Unit tests (no DB): Verify Pydantic schema parsing logic.
2. Integration tests (DB required): Verify actual Django QuerySet mutation.
"""

from __future__ import annotations

import pytest

from nova.query.planner import analyze_schema_for_relations
from tests.models import ArticleWithRelations, ArticleWithRelationsSchema


class TestPlannerAnalysis:
    """
    Unit tests for schema introspection.
    No database required, as we only analyze Pydantic models.
    """

    def test_detects_select_related(self) -> None:
        """Planner must detect a nested BaseModel as a select_related candidate."""
        hints = analyze_schema_for_relations(ArticleWithRelationsSchema)

        assert "select" in hints
        assert "prefetch" in hints
        assert "author" in hints["select"]
        assert "tags" not in hints["select"]

    def test_detects_prefetch_related(self) -> None:
        """Planner must detect list[BaseModel] as a prefetch_related candidate."""
        hints = analyze_schema_for_relations(ArticleWithRelationsSchema)

        assert "tags" in hints["prefetch"]
        assert "author" not in hints["prefetch"]

    def test_respects_exclude_from_pydantic(self) -> None:
        """Planner must completely ignore fields listed in NovaConfig.exclude_from_pydantic."""
        hints = analyze_schema_for_relations(
            ArticleWithRelationsSchema,
            exclude=("author",)
        )

        assert "author" not in hints["select"]
        # Tags should still be detected
        assert "tags" in hints["prefetch"]

    def test_returns_empty_for_plain_models(self) -> None:
        """Planner must return empty dicts if no relations are in the schema."""
        from tests.models import LabSchema

        hints = analyze_schema_for_relations(LabSchema)
        assert hints["select"] == []
        assert hints["prefetch"] == []


class TestPlannerIntegration:
    """
    Integration tests verifying QuerySet mutation.
    Requires real Django database tables to inspect generated SQL.
    """

    @pytest.mark.django_db
    def test_auto_applies_select_and_prefetch(self) -> None:
        """
        Core integration test.
        The .auto() method must inject SQL JOINs based on the Pydantic schema.
        """
        qs = ArticleWithRelations.objects.all()

        # 1. Baseline check: Default Django QuerySet has no JOINs
        baseline_sql = str(qs.query).upper()
        assert "JOIN" not in baseline_sql

        # 2. Apply the Schema-Driven Query Planner
        optimized_qs = qs.auto()

        # 3. Post-optimization check: SQL must contain JOINs for ForeignKey
        optimized_sql = str(optimized_qs.query).upper()
        assert "JOIN" in optimized_sql

    @pytest.mark.django_db
    def test_auto_preserves_filters(self) -> None:
        """
        Planner must not break standard QuerySet chaining.
        Filters applied before .auto() must remain in the final SQL.
        """
        qs = ArticleWithRelations.objects.filter(title__icontains="django")
        optimized_qs = qs.auto()

        sql = str(optimized_qs.query).upper()
        # Verify the filter is still there
        assert "WHERE" in sql
        # Verify the join was added
        assert "JOIN" in sql
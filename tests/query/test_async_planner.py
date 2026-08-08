"""P0-3.5 async planner contract tests."""

from __future__ import annotations

import pytest

from nova.query.planner import build_query_plan
from tests.models import ArticleDeep, ArticleWithRelations, AsyncArticle


def test_build_query_plan_fk_select_related() -> None:
    plan = build_query_plan(ArticleWithRelations)
    assert "author" in plan.select_related


def test_build_query_plan_m2m_prefetch_related() -> None:
    plan = build_query_plan(ArticleWithRelations)
    assert "tags" in plan.prefetch_related


def test_build_query_plan_deep_nested() -> None:
    """select_related('author__profile') already implies joining 'author'."""
    plan = build_query_plan(ArticleDeep)
    assert "author__profile" in plan.select_related
    assert "tags" in plan.prefetch_related


def test_build_query_plan_empty_for_no_schema() -> None:
    from tests.models import Article

    assert build_query_plan(Article).is_empty()


def test_build_query_plan_deterministic() -> None:
    plan1 = build_query_plan(ArticleWithRelations)
    plan2 = build_query_plan(ArticleWithRelations)
    assert plan1.select_related == plan2.select_related
    assert plan1.prefetch_related == plan2.prefetch_related


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_aauto_idempotent() -> None:
    qs1 = AsyncArticle.objects.all().aauto()
    qs2 = AsyncArticle.objects.all().aauto()
    assert str(qs1.query) == str(qs2.query)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_alist_materializes() -> None:
    result = await AsyncArticle.objects.all().alist()
    assert isinstance(result, list)

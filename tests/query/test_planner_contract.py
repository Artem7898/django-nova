"""Query core planner contracts (FK / M2M / reverse / determinism / execution)."""

from __future__ import annotations

import pytest

from nova.query.planner import QueryPlan, apply_plan, build_query_plan
from tests.models import ArticleWithRelations, Hub, Post
from tests.query.contracts import PlannerExpectation, QueryPlannerContract


def test_fk_select_related() -> None:
    contract = QueryPlannerContract(
        PlannerExpectation(
            target="Post FK",
            model=Post,
            expected_select=frozenset({"author"}),
            expected_deferred=frozenset({"body", "views"}),
            forbidden_defer=frozenset({"id", "title", "author_id"}),
        )
    )
    plan = contract.check_plan()
    contract.check_deterministic()
    contract.check_explain_stable(plan)
    contract.check_execution(plan)


def test_m2m_prefetch_related() -> None:
    contract = QueryPlannerContract(
        PlannerExpectation(
            target="ArticleWithRelations M2M",
            model=ArticleWithRelations,
            expected_select=frozenset({"author"}),
            expected_prefetch=frozenset({"tags"}),
        )
    )
    contract.check_plan()
    contract.check_deterministic()


def test_reverse_relation_prefetch() -> None:
    contract = QueryPlannerContract(
        PlannerExpectation(
            target="Hub reverse FK",
            model=Hub,
            expected_prefetch=frozenset({"items"}),
            exact=False,
        )
    )
    contract.check_plan()


def test_plan_sorted_and_deterministic() -> None:
    plans = [build_query_plan(ArticleWithRelations) for _ in range(5)]
    assert all(p == plans[0] for p in plans)
    assert plans[0].select_related == sorted(plans[0].select_related)
    assert plans[0].prefetch_related == sorted(plans[0].prefetch_related)


def test_only_mode_executor() -> None:
    """only(): Django stores the LOADED set with flag False."""
    plan = QueryPlan(only=["id", "title", "author_id"])
    qs = apply_plan(Post.objects.all(), plan)
    loaded, defer_flag = qs.query.deferred_loading
    assert defer_flag is False
    assert set(loaded) == {"id", "title", "author_id"}


def test_queryplan_explain_stable() -> None:
    contract = QueryPlannerContract(
        PlannerExpectation(
            target="Post explain",
            model=Post,
            expected_select=frozenset({"author"}),
            expected_deferred=frozenset({"body", "views"}),
        )
    )
    plan = contract.check_plan()
    contract.check_explain_stable(plan)


def test_queryplan_equality_is_value_based() -> None:
    p1 = QueryPlan(select_related=["author"], defer=["body"])
    p2 = QueryPlan(select_related=["author"], defer=["body"])
    assert p1 == p2


def test_queryplan_is_immutable() -> None:
    from dataclasses import FrozenInstanceError

    plan = build_query_plan(Post)
    with pytest.raises(FrozenInstanceError):
        plan.select_related = ["hacked"]  # type: ignore[misc]


def test_queryplan_str_is_human_readable() -> None:
    text = str(build_query_plan(Post))
    assert "select_related" in text
    assert "defer" in text


def test_empty_plan_for_schema_less_model() -> None:
    from tests.models import Article

    assert build_query_plan(Article).is_empty()
    assert str(build_query_plan(Article)) == "QueryPlan(empty)"

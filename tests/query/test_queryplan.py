"""P0-4: QueryPlan data structure contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from nova.query.planner import QueryPlan, build_query_plan
from tests.models import Article, Post
from tests.query.contracts import PlannerExpectation, QueryPlannerContract


def test_queryplan_equality_is_value_based() -> None:
    assert QueryPlan(select_related=["a"]) == QueryPlan(select_related=["a"])


def test_queryplan_is_immutable() -> None:
    plan = build_query_plan(Post)
    with pytest.raises(FrozenInstanceError):
        plan.defer = ["x"]  # type: ignore[misc]


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


def test_queryplan_explain_returns_correct_structure() -> None:
    plan = QueryPlan(select_related=["author"], defer=["body"])
    expl = plan.explain()
    assert expl["select_related"] == ("author",)
    assert expl["defer"] == ("body",)
    assert expl["only"] == ()
    assert expl["prefetch_related"] == ()


def test_queryplan_str_is_human_readable() -> None:
    assert "select_related" in str(build_query_plan(Post))


def test_empty_plan_for_schema_less_model() -> None:
    assert build_query_plan(Article).is_empty()

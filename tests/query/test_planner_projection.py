"""P0-4: only()/defer() projection correctness."""

from __future__ import annotations

from nova.query.planner import QueryPlan, apply_plan, build_query_plan
from tests.models import Post


def test_defer_never_touches_pk_or_fk_attname() -> None:
    plan = build_query_plan(Post)
    deferred = set(plan.defer)
    assert not (deferred & {"id", "author_id", "title"})
    assert {"body", "views"} <= deferred


def test_defer_mode_executor() -> None:
    """defer(): deferred_loading holds the deferred set with flag True."""
    plan = build_query_plan(Post)
    qs = apply_plan(Post.objects.all(), plan)
    deferred, defer_flag = qs.query.deferred_loading
    assert defer_flag is True
    assert {"body", "views"} <= set(deferred)


def test_only_mode_executor() -> None:
    """only(): deferred_loading holds the LOADED set with flag False."""
    plan = QueryPlan(only=["id", "title", "author_id"])
    qs = apply_plan(Post.objects.all(), plan)
    loaded, defer_flag = qs.query.deferred_loading
    assert defer_flag is False
    assert set(loaded) == {"id", "title", "author_id"}


def test_select_related_keeps_fk_integrity() -> None:
    plan = build_query_plan(Post)
    qs = apply_plan(Post.objects.all(), plan)
    assert qs.query.select_related
    assert "author_id" not in set(qs.query.deferred_loading[0])

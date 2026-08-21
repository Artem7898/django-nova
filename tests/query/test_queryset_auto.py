"""Query: public API .auto() contracts."""

from __future__ import annotations

from tests.models import Post


def test_auto_applies_select_and_defer() -> None:
    qs = Post.objects.all().auto()
    assert qs.query.select_related
    assert {"body", "views"} <= set(qs.query.deferred_loading[0])


def test_auto_preserves_filters() -> None:
    qs = Post.objects.filter(title__icontains="nova").auto()
    assert qs.query.where is not None


def test_auto_idempotent() -> None:
    qs1 = Post.objects.all().auto()
    qs2 = Post.objects.all().auto()
    assert str(qs1.query) == str(qs2.query)

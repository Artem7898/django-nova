"""Tests for typed Django QuerySet wrappers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.db import models

from nova.query.planner import QueryPlan
from nova.typing.models import NovaModel
from nova.typing.querysets import TypedQuerySet


class SampleModel(NovaModel):
    """Minimal NovaModel used for QuerySet typing tests."""

    name = models.CharField(max_length=100)

    class Meta:
        app_label = "tests"


class TestTypedQuerySet:
    """Tests for TypedQuerySet behavior."""

    def make_queryset(self) -> TypedQuerySet[SampleModel]:
        return TypedQuerySet(model=SampleModel)

    def test_iter_casts_parent_iterator(self) -> None:
        queryset = self.make_queryset()
        expected = iter([SampleModel(name="one"), SampleModel(name="two")])

        with patch(
            "django.db.models.QuerySet.__iter__",
            return_value=expected,
        ):
            result = TypedQuerySet.__iter__(queryset)

        assert result is expected

    def test_getitem_with_integer_returns_model(self) -> None:
        queryset = self.make_queryset()
        instance = SampleModel(name="one")

        with patch(
            "django.db.models.QuerySet.__getitem__",
            return_value=instance,
        ):
            result = TypedQuerySet.__getitem__(queryset, 0)

        assert result is instance

    def test_getitem_with_slice_returns_queryset(self) -> None:
        queryset = self.make_queryset()
        sliced_queryset = MagicMock(spec=TypedQuerySet)

        with patch(
            "django.db.models.QuerySet.__getitem__",
            return_value=sliced_queryset,
        ):
            result = TypedQuerySet.__getitem__(queryset, slice(1, 3))

        assert result is sliced_queryset

    def test_first_returns_model(self) -> None:
        queryset = self.make_queryset()
        instance = SampleModel(name="first")

        with patch(
            "django.db.models.QuerySet.first",
            return_value=instance,
        ):
            result = TypedQuerySet.first(queryset)

        assert result is instance

    def test_first_returns_none(self) -> None:
        queryset = self.make_queryset()

        with patch(
            "django.db.models.QuerySet.first",
            return_value=None,
        ):
            result = TypedQuerySet.first(queryset)

        assert result is None

    def test_last_returns_model(self) -> None:
        queryset = self.make_queryset()
        instance = SampleModel(name="last")

        with patch(
            "django.db.models.QuerySet.last",
            return_value=instance,
        ):
            result = TypedQuerySet.last(queryset)

        assert result is instance

    def test_using_replica_sets_and_clears_state(self) -> None:
        queryset = self.make_queryset()
        replica_state = MagicMock()

        with patch(
            "nova.db.router.replica_state",
            replica_state,
        ):
            with queryset.using_replica() as result:
                assert result is queryset
                replica_state.set_read_from_replica.assert_called_once_with()
                replica_state.clear_replica_state.assert_not_called()

            replica_state.clear_replica_state.assert_called_once_with()

    def test_using_replica_clears_state_on_exception(self) -> None:
        queryset = self.make_queryset()
        replica_state = MagicMock()

        with patch(
            "nova.db.router.replica_state",
            replica_state,
        ):
            try:
                with queryset.using_replica():
                    raise RuntimeError("test failure")
            except RuntimeError:
                pass

        replica_state.set_read_from_replica.assert_called_once_with()
        replica_state.clear_replica_state.assert_called_once_with()

    def test_get_plan_delegates_to_build_query_plan(self) -> None:
        queryset = self.make_queryset()
        plan = MagicMock(spec=QueryPlan)

        with patch(
            "nova.typing.querysets.build_query_plan",
            return_value=plan,
        ) as build_query_plan:
            result = queryset.get_plan()

        assert result is plan
        build_query_plan.assert_called_once_with(SampleModel)

    def test_apply_plan_delegates_to_apply_plan(self) -> None:
        queryset = self.make_queryset()
        plan = MagicMock(spec=QueryPlan)
        optimized_queryset = MagicMock(spec=TypedQuerySet)

        with patch(
            "nova.typing.querysets.apply_plan",
            return_value=optimized_queryset,
        ) as apply_plan:
            result = queryset.apply_plan(plan)

        assert result is optimized_queryset
        apply_plan.assert_called_once_with(queryset, plan)

    def test_auto_delegates_to_apply_optimizations(self) -> None:
        queryset = self.make_queryset()
        optimized_queryset = MagicMock(spec=TypedQuerySet)

        with patch(
            "nova.typing.querysets.apply_optimizations",
            return_value=optimized_queryset,
        ) as apply_optimizations:
            result = queryset.auto()

        assert result is optimized_queryset
        apply_optimizations.assert_called_once_with(queryset, SampleModel)

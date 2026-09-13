"""Tests for chunked data migrations."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.db.migrations import RunPython

from nova.db.splitter import chunked_migration


class FakeQuerySet:
    """Minimal QuerySet-like object for unit testing migration batching."""

    def __init__(self, batches: list[list[int]]) -> None:
        self._batches = list(batches)
        self._current_index = 0

    def exists(self) -> bool:
        """Return whether another batch is available."""
        return self._current_index < len(self._batches)

    def values_list(self, field: str, flat: bool = False) -> FakeQuerySet:
        """Return this queryset for chained values_list()."""
        assert field == "pk"
        assert flat is True
        return self

    def __getitem__(self, value: slice) -> list[int]:
        """Return the next configured batch, respecting the slice."""
        if not isinstance(value, slice):
            raise TypeError("FakeQuerySet only supports slicing")

        batch = self._batches[self._current_index]
        return batch[: value.stop]

    def filter(self, **kwargs: int) -> FakeQuerySet:
        """Advance to the next batch using the last processed PK."""
        assert "pk__gt" in kwargs
        self._current_index += 1
        return self

    def order_by(self, field: str) -> FakeQuerySet:
        """Return this queryset for chained order_by()."""
        assert field == "pk"
        return self


class FakeManager:
    """Minimal model manager exposing objects.all()."""

    def __init__(self, queryset: FakeQuerySet) -> None:
        self.queryset = queryset

    def all(self) -> FakeQuerySet:
        """Return the configured fake queryset."""
        return self.queryset


class FakeModel:
    """Minimal model exposing a Django-like objects manager."""

    def __init__(self, queryset: FakeQuerySet) -> None:
        self.objects = FakeManager(queryset)


class FakeApps:
    """Minimal apps registry exposing get_model()."""

    def __init__(self, model: FakeModel) -> None:
        self.model = model

    def get_model(self, app_label: str, model_name: str) -> FakeModel:
        """Return the configured fake model."""
        assert app_label == "app"
        assert model_name == "Model"
        return self.model


def make_apps(batches: list[list[int]]) -> FakeApps:
    """Build a fake Django apps registry for migration tests."""
    return FakeApps(FakeModel(FakeQuerySet(batches)))


class TestChunkedMigration:
    """Tests for chunked_migration()."""

    def test_returns_run_python_operation(self) -> None:
        func = MagicMock()

        operation = chunked_migration(func)

        assert isinstance(operation, RunPython)
        assert operation.reverse_code is RunPython.noop

    def test_does_not_call_function_for_empty_queryset(self) -> None:
        apps = make_apps([])
        schema_editor = MagicMock()
        func = MagicMock()

        operation = chunked_migration(func)

        with patch("nova.db.splitter.transaction.atomic"):
            operation.code(apps, schema_editor)

        func.assert_not_called()

    def test_processes_single_batch(self) -> None:
        apps = make_apps([[1, 2, 3]])
        schema_editor = MagicMock()
        func = MagicMock()

        operation = chunked_migration(func, batch_size=3)

        with patch("nova.db.splitter.transaction.atomic") as atomic:
            operation.code(apps, schema_editor)

        assert func.call_count == 1
        func.assert_called_once_with(
            apps,
            schema_editor,
            pks=[1, 2, 3],
        )
        atomic.assert_called_once_with()

    def test_processes_multiple_batches(self) -> None:
        apps = make_apps(
            [
                [1, 2],
                [3, 4],
                [5],
            ]
        )
        schema_editor = MagicMock()
        func = MagicMock()

        operation = chunked_migration(func, batch_size=2)

        with patch("nova.db.splitter.transaction.atomic"):
            operation.code(apps, schema_editor)

        assert func.call_count == 3
        assert func.call_args_list == [
            ((apps, schema_editor), {"pks": [1, 2]}),
            ((apps, schema_editor), {"pks": [3, 4]}),
            ((apps, schema_editor), {"pks": [5]}),
        ]

    def test_uses_configured_batch_size(self) -> None:
        apps = make_apps([[10, 20, 30, 40]])
        schema_editor = MagicMock()
        func = MagicMock()

        operation = chunked_migration(func, batch_size=2)

        with patch("nova.db.splitter.transaction.atomic"):
            operation.code(apps, schema_editor)

        func.assert_called_once_with(
            apps,
            schema_editor,
            pks=[10, 20],
        )

    def test_respects_batch_size_across_multiple_batches(self) -> None:
        apps = make_apps(
            [
                [1, 2, 3, 4, 5],
                [6, 7],
            ]
        )
        schema_editor = MagicMock()
        func = MagicMock()

        operation = chunked_migration(func, batch_size=2)

        with patch("nova.db.splitter.transaction.atomic"):
            operation.code(apps, schema_editor)

        assert [call.kwargs["pks"] for call in func.call_args_list] == [
            [1, 2],
            [6, 7],
        ]

    def test_wraps_each_batch_in_atomic_transaction(self) -> None:
        apps = make_apps(
            [
                [1, 2],
                [3],
            ]
        )
        schema_editor = MagicMock()
        func = MagicMock()

        operation = chunked_migration(func, batch_size=2)

        with patch("nova.db.splitter.transaction.atomic") as atomic:
            atomic.return_value.__enter__ = MagicMock()
            atomic.return_value.__exit__ = MagicMock(return_value=False)

            operation.code(apps, schema_editor)

        assert atomic.call_count == 2

    def test_filters_next_batch_by_last_processed_pk(self) -> None:
        queryset = FakeQuerySet(
            [
                [10, 20],
                [30],
                [40],
            ]
        )
        apps = FakeApps(FakeModel(queryset))
        schema_editor = MagicMock()
        func = MagicMock()

        with patch.object(queryset, "filter", wraps=queryset.filter) as filter_method:
            operation = chunked_migration(func, batch_size=2)

            with patch("nova.db.splitter.transaction.atomic"):
                operation.code(apps, schema_editor)

        assert [call_args.kwargs for call_args in filter_method.call_args_list] == [
            {"pk__gt": 20},
            {"pk__gt": 30},
            {"pk__gt": 40},
        ]

    def test_logs_processed_batch(self, caplog) -> None:
        apps = make_apps(
            [
                [101, 102],
                [103],
            ]
        )
        schema_editor = MagicMock()
        func = MagicMock()

        operation = chunked_migration(func, batch_size=2)

        with (
            caplog.at_level("INFO", logger="nova.db.splitter"),
            patch("nova.db.splitter.transaction.atomic"),
        ):
            operation.code(apps, schema_editor)

        messages = [record.getMessage() for record in caplog.records]

        assert messages == [
            "Processed batch of 2, last pk: 102",
            "Processed batch of 2, last pk: 103",
        ]

    def test_propagates_function_exception(self) -> None:
        apps = make_apps([[1, 2]])
        schema_editor = MagicMock()
        func = MagicMock(side_effect=RuntimeError("migration failed"))

        operation = chunked_migration(func, batch_size=2)

        with (
            patch("nova.db.splitter.transaction.atomic"),
            __import__("pytest").raises(RuntimeError, match="migration failed"),
        ):
            operation.code(apps, schema_editor)

    def test_does_not_process_batches_after_function_failure(self) -> None:
        apps = make_apps(
            [
                [1, 2],
                [3, 4],
            ]
        )
        schema_editor = MagicMock()
        func = MagicMock(side_effect=RuntimeError("migration failed"))

        operation = chunked_migration(func, batch_size=2)

        with (
            patch("nova.db.splitter.transaction.atomic"),
            __import__("pytest").raises(RuntimeError, match="migration failed"),
        ):
            operation.code(apps, schema_editor)

        func.assert_called_once_with(
            apps,
            schema_editor,
            pks=[1, 2],
        )

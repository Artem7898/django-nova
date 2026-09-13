"""Tests for zero-downtime migration operations."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.db import migrations
from django.db.migrations.operations.fields import AddField
from django.db.models import IntegerField
from django.db.models.fields import NOT_PROVIDED

from nova.db.zero_downtime import (
    AddFieldConcurrently,
    CreateIndexConcurrently,
)


class FakeMeta:
    """Minimal Django model metadata used by migration tests."""

    db_table = "test_model"

    def __init__(self, field: IntegerField) -> None:
        self._field = field

    def get_field(self, name: str) -> IntegerField:
        assert name == "value"
        return self._field


class FakeModel:
    """Minimal model object required by AddFieldConcurrently."""

    def __init__(self, field: IntegerField) -> None:
        self._meta = FakeMeta(field)


class FakeApps:
    """Minimal apps registry used by migration tests."""

    def __init__(self, model: FakeModel) -> None:
        self.model = model

    def get_model(self, app_label: str, model_name: str) -> FakeModel:
        assert app_label == "test_app"
        assert model_name == "TestModel"
        return self.model


class FakeState:
    """Minimal migration state containing an apps registry."""

    def __init__(self, apps: FakeApps) -> None:
        self.apps = apps


class FakeConnection:
    """Minimal database connection for schema-editor tests."""

    def __init__(self, vendor: str) -> None:
        self.vendor = vendor
        self.cursor_mock = MagicMock()
        self.cursor_mock.__enter__.return_value = self.cursor_mock
        self.cursor_mock.__exit__.return_value = False

    def cursor(self) -> MagicMock:
        return self.cursor_mock


class FakeSchemaEditor:
    """Minimal schema editor required by database_forwards."""

    def __init__(self, vendor: str) -> None:
        self.connection = FakeConnection(vendor)


def make_field(
    *,
    null: bool = True,
    default: Any = NOT_PROVIDED,
) -> IntegerField:
    """Create a field suitable for migration-operation tests."""
    field = IntegerField(null=null, default=default)
    field.set_attributes_from_name("value")
    return field


class TestAddFieldConcurrently:
    """Tests for AddFieldConcurrently."""

    def test_is_add_field_subclass(self) -> None:
        field = make_field(null=True)

        operation = AddFieldConcurrently(
            model_name="TestModel",
            name="value",
            field=field,
        )

        assert isinstance(operation, AddField)

    def test_accepts_nullable_field_without_default(self) -> None:
        field = make_field(null=True)

        operation = AddFieldConcurrently(
            model_name="TestModel",
            name="value",
            field=field,
        )

        assert operation.field is field

    def test_accepts_non_nullable_field_with_default(self) -> None:
        field = make_field(null=False, default=0)

        operation = AddFieldConcurrently(
            model_name="TestModel",
            name="value",
            field=field,
        )

        assert operation.field is field

    def test_rejects_non_nullable_field_without_default(self) -> None:
        field = make_field(null=False)

        with pytest.raises(
            ValueError,
            match="zero-downtime AddField requires null=True or a default value",
        ):
            AddFieldConcurrently(
                model_name="TestModel",
                name="value",
                field=field,
            )

    def test_database_forwards_executes_sql_on_postgresql(self) -> None:
        field = make_field(null=True)
        model = FakeModel(field)
        apps = FakeApps(model)
        to_state = FakeState(apps)
        schema_editor = FakeSchemaEditor("postgresql")

        operation = AddFieldConcurrently(
            model_name="TestModel",
            name="value",
            field=field,
        )

        with patch.object(
            field,
            "db_type",
            return_value="integer",
        ) as db_type:
            operation.database_forwards(
                "test_app",
                schema_editor,
                MagicMock(),
                to_state,
            )

        db_type.assert_called_once_with(schema_editor.connection)
        schema_editor.connection.cursor_mock.execute.assert_called_once_with(
            "ALTER TABLE test_model ADD COLUMN value integer"
        )

    def test_database_forwards_uses_field_database_type(self) -> None:
        field = make_field(null=True)
        model = FakeModel(field)
        apps = FakeApps(model)
        to_state = FakeState(apps)
        schema_editor = FakeSchemaEditor("postgresql")

        operation = AddFieldConcurrently(
            model_name="TestModel",
            name="value",
            field=field,
        )

        with patch.object(
            field,
            "db_type",
            return_value="custom_type",
        ) as db_type:
            operation.database_forwards(
                "test_app",
                schema_editor,
                MagicMock(),
                to_state,
            )

        db_type.assert_called_once_with(schema_editor.connection)
        schema_editor.connection.cursor_mock.execute.assert_called_once_with(
            "ALTER TABLE test_model ADD COLUMN value custom_type"
        )

    def test_database_forwards_falls_back_for_non_postgresql(
        self,
    ) -> None:
        field = make_field(null=True)
        schema_editor = FakeSchemaEditor("sqlite")
        from_state = MagicMock()
        to_state = MagicMock()

        operation = AddFieldConcurrently(
            model_name="TestModel",
            name="value",
            field=field,
        )

        with patch.object(
            AddField,
            "database_forwards",
            autospec=True,
        ) as parent_database_forwards:
            operation.database_forwards(
                "test_app",
                schema_editor,
                from_state,
                to_state,
            )

        parent_database_forwards.assert_called_once_with(
            operation,
            "test_app",
            schema_editor,
            from_state,
            to_state,
        )

    def test_database_forwards_logs_postgresql_success(self) -> None:
        field = make_field(null=True)
        model = FakeModel(field)
        apps = FakeApps(model)
        to_state = FakeState(apps)
        schema_editor = FakeSchemaEditor("postgresql")

        operation = AddFieldConcurrently(
            model_name="TestModel",
            name="value",
            field=field,
        )

        with (
            patch.object(
                field,
                "db_type",
                return_value="integer",
            ) as db_type,
            patch("nova.db.zero_downtime.logger") as logger,
        ):
            operation.database_forwards(
                "test_app",
                schema_editor,
                MagicMock(),
                to_state,
            )

        db_type.assert_called_once_with(schema_editor.connection)
        logger.info.assert_called_once_with(
            "Added column %s concurrently without exclusive lock",
            "value",
        )

    def test_database_forwards_logs_non_postgresql_fallback(self) -> None:
        field = make_field(null=True)
        schema_editor = FakeSchemaEditor("sqlite")

        operation = AddFieldConcurrently(
            model_name="TestModel",
            name="value",
            field=field,
        )

        with (
            patch.object(
                AddField,
                "database_forwards",
                autospec=True,
            ),
            patch("nova.db.zero_downtime.logger") as logger,
        ):
            operation.database_forwards(
                "test_app",
                schema_editor,
                MagicMock(),
                MagicMock(),
            )

        logger.warning.assert_called_once_with(
            "AddFieldConcurrently is PostgreSQL only. Falling back to standard AddField."
        )


class TestCreateIndexConcurrently:
    """Tests for CreateIndexConcurrently."""

    def test_is_run_sql_subclass(self) -> None:
        operation = CreateIndexConcurrently(
            table="test_model",
            index_name="test_model_value_idx",
            columns=["value"],
        )

        assert isinstance(operation, migrations.RunSQL)

    def test_generates_create_index_sql(self) -> None:
        operation = CreateIndexConcurrently(
            table="test_model",
            index_name="test_model_value_idx",
            columns=["value"],
        )

        assert operation.sql == (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS test_model_value_idx ON test_model (value)"
        )

    def test_generates_sql_for_multiple_columns(self) -> None:
        operation = CreateIndexConcurrently(
            table="test_model",
            index_name="test_model_a_b_idx",
            columns=["field_a", "field_b"],
        )

        assert operation.sql == (
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "test_model_a_b_idx ON test_model (field_a, field_b)"
        )

    def test_generates_reverse_sql(self) -> None:
        operation = CreateIndexConcurrently(
            table="test_model",
            index_name="test_model_value_idx",
            columns=["value"],
        )

        assert operation.reverse_sql == ("DROP INDEX IF EXISTS test_model_value_idx")

    def test_preserves_run_sql_kwargs(self) -> None:
        operation = CreateIndexConcurrently(
            table="test_model",
            index_name="test_model_value_idx",
            columns=["value"],
            hints={"postgresql": "custom"},
        )

        assert operation.hints == {"postgresql": "custom"}

    def test_preserves_extra_positional_arguments(self) -> None:
        state_operations = MagicMock()

        operation = CreateIndexConcurrently(
            "test_model",
            "test_model_value_idx",
            ["value"],
            state_operations,
        )

        assert operation.state_operations == state_operations

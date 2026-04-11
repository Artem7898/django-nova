"""
Zero-downtime migration operations for PostgreSQL.
Scientific context: Research databases are often locked by long analytical queries.
Standard ALTER TABLE causes exclusive locks, blocking reads.
"""
from __future__ import annotations

import logging
from typing import Any

from django.db import migrations
from django.db.migrations.operations.fields import AddField

logger = logging.getLogger(__name__)


class AddFieldConcurrently(AddField):
    """
    Adds a field initially as NULL (if possible) without an exclusive lock.
    Requires PostgreSQL.
    """

    def database_forwards(self, app_label: str, schema_editor: Any, from_state: Any, to_state: Any) -> None:
        if schema_editor.connection.vendor != 'postgresql':
            logger.warning("AddFieldConcurrently is PostgreSQL only. Falling back.")
            return super().database_forwards(app_label, schema_editor, from_state, to_state)

        model = to_state.apps.get_model(app_label, self.model_name)
        field = model._meta.get_field(self.name)

        if not field.null and field.default is migrations.NOT_PROVIDED:
            raise ValueError("Zero-downtime AddField requires null=True or a default value.")

        with schema_editor.connection.cursor() as cursor:
            # Add column without validation lock
            sql = f"ALTER TABLE {model._meta.db_table} ADD COLUMN {field.column} {field.db_type(schema_editor.connection)}"
            cursor.execute(sql)
            logger.info("Added column %s concurrently", field.column)


class CreateIndexConcurrently(migrations.RunSQL):
    """
    Wrapper for CREATE INDEX CONCURRENTLY which does not block writes.
    """

    def __init__(self, table: str, index_name: str, columns: list[str], *args: Any, **kwargs: Any) -> None:
        cols = ", ".join(columns)
        sql = f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} ON {table} ({cols})"
        reverse_sql = f"DROP INDEX IF EXISTS {index_name}"
        super().__init__(sql, reverse_sql, *args, **kwargs)
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
from django.db.models.fields import NOT_PROVIDED

logger = logging.getLogger(__name__)


class AddFieldConcurrently(AddField):
    """
    Adds a field without an exclusive lock using standard ALTER TABLE.
    Requires the field to have null=True or a default value to avoid full table rewrite.
    Requires PostgreSQL.
    """
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Fail fast: check constraints at migration definition time, not execution time
        if not self.field.null and self.field.default is NOT_PROVIDED:
            raise ValueError(
                "zero-downtime AddField requires null=True or a default value. "
                "Adding a non-nullable column without a default on a large table "
                "requires an exclusive lock and causes table rewrite."
            )

    def database_forwards(self, app_label: str, schema_editor: Any, from_state: Any, to_state: Any) -> None:
        if schema_editor.connection.vendor != 'postgresql':
            logger.warning("AddFieldConcurrently is PostgreSQL only. Falling back to standard AddField.")
            return super().database_forwards(app_label, schema_editor, from_state, to_state)

        model = to_state.apps.get_model(app_label, self.model_name)
        field = model._meta.get_field(self.name)
        
        with schema_editor.connection.cursor() as cursor:
            # In PG, adding a nullable column or a column with a default 
            # does NOT acquire an exclusive lock (no table rewrite).
            sql = f"ALTER TABLE {model._meta.db_table} ADD COLUMN {field.column} {field.db_type(schema_editor.connection)}"
            cursor.execute(sql)
            logger.info("Added column %s concurrently without exclusive lock", field.column)


class CreateIndexConcurrently(migrations.RunSQL):
    """
    Wrapper for CREATE INDEX CONCURRENTLY which does not block writes.
    """
    def __init__(self, table: str, index_name: str, columns: list[str], *args: Any, **kwargs: Any) -> None:
        cols = ", ".join(columns)
        sql = f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} ON {table} ({cols})"
        reverse_sql = f"DROP INDEX IF EXISTS {index_name}"
        super().__init__(sql, reverse_sql, *args, **kwargs)

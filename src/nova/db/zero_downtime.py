"""
Experimental PostgreSQL migration helpers.

These names do not imply lock-free execution. Review emitted SQL, transaction
requirements, field options, and reverse operations before use.
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
    Issue a minimal ALTER TABLE ADD COLUMN statement on PostgreSQL.

    The constructor requires null=True or a default. The PostgreSQL SQL path
    uses only the column name and database type; it does not reproduce all
    Django field options, defaults, or constraints. ALTER TABLE still takes
    a table lock. Other databases use Django's standard AddField operation.
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

    def database_forwards(
        self, app_label: str, schema_editor: Any, from_state: Any, to_state: Any
    ) -> None:
        if schema_editor.connection.vendor != "postgresql":
            logger.warning(
                "AddFieldConcurrently is PostgreSQL only. Falling back to standard AddField."
            )
            return super().database_forwards(app_label, schema_editor, from_state, to_state)

        model = to_state.apps.get_model(app_label, self.model_name)
        field = model._meta.get_field(self.name)

        with schema_editor.connection.cursor() as cursor:
            # Avoiding a table rewrite does not eliminate the ALTER TABLE lock.
            sql = f"ALTER TABLE {model._meta.db_table} ADD COLUMN {field.column} {field.db_type(schema_editor.connection)}"
            cursor.execute(sql)
            logger.info("Added column %s concurrently without exclusive lock", field.column)


class CreateIndexConcurrently(migrations.RunSQL):
    """
    Emit PostgreSQL CREATE INDEX CONCURRENTLY in a non-atomic migration.

    PostgreSQL disallows the forward command inside a transaction block.
    The reverse command is an ordinary DROP INDEX; inspect its locking
    behavior separately. This helper is PostgreSQL-specific.
    """

    def __init__(
        self, table: str, index_name: str, columns: list[str], *args: Any, **kwargs: Any
    ) -> None:
        cols = ", ".join(columns)
        sql = f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} ON {table} ({cols})"
        reverse_sql = f"DROP INDEX IF EXISTS {index_name}"
        super().__init__(sql, reverse_sql, *args, **kwargs)

"""Apply and reverse real TypedField DDL against the selected test database.

Uses Migration.apply/unapply and historical models from ProjectState. Does not
modify the project's migration graph or django_migrations recorder.
"""

from decimal import Decimal
from uuid import UUID

import pytest
from django.db import IntegrityError, connection, migrations, models, transaction
from django.db.migrations.state import ProjectState

from nova.typing.fields import TypedField

APP_LABEL = "nova_field_migration_contract"
TABLE_NAME = "nova_test_typed_field_migration"


class MigrationHarness:
    def __init__(self):
        self.state = ProjectState()
        self.applied = []

    def apply(self, *operations):
        migration = migrations.Migration(f"step_{len(self.applied)}", APP_LABEL)
        migration.operations = list(operations)
        before = self.state.clone()
        with connection.schema_editor() as editor:
            after = migration.apply(self.state.clone(), editor)
        self.applied.append((migration, before))
        self.state = after
        return self.model()

    def reverse(self):
        migration, before = self.applied[-1]
        with connection.schema_editor() as editor:
            migration.unapply(before.clone(), editor)
        self.applied.pop()
        self.state = before

    def model(self):
        return self.state.apps.get_model(APP_LABEL, "Record")

    def create(self, field):
        return self.apply(
            migrations.CreateModel(
                name="Record",
                fields=[
                    ("id", models.BigAutoField(primary_key=True)),
                    ("value", field),
                ],
                options={"db_table": TABLE_NAME},
            )
        )


@pytest.fixture
def migration_db(transactional_db):
    harness = MigrationHarness()
    try:
        yield harness
    finally:
        # Drop only this fixture's table, even if a reverse assertion failed.
        if TABLE_NAME in connection.introspection.table_names():
            with connection.schema_editor() as editor:
                editor.delete_model(harness.model())


def column_info(name):
    with connection.cursor() as cursor:
        columns = connection.introspection.get_table_description(cursor, TABLE_NAME)
    return next(column for column in columns if column.name == name)


@pytest.mark.parametrize(
    ("inner", "raw", "expected"),
    [
        pytest.param(models.IntegerField(), "42", 42, id="integer"),
        pytest.param(
            models.DecimalField(max_digits=8, decimal_places=2),
            "12.50",
            Decimal("12.50"),
            id="decimal",
        ),
        pytest.param(
            models.UUIDField(),
            "12345678-1234-5678-1234-567812345678",
            UUID("12345678-1234-5678-1234-567812345678"),
            id="uuid",
        ),
        pytest.param(models.JSONField(), {"items": [1, None]}, {"items": [1, None]}, id="json"),
    ],
)
def test_create_model_and_reverse(migration_db, inner, raw, expected):
    record = migration_db.create(TypedField(inner))
    assert TABLE_NAME in connection.introspection.table_names()
    assert isinstance(record._meta.get_field("value"), TypedField)
    instance = record.objects.create(value=raw)
    instance.refresh_from_db()
    assert instance.value == expected
    assert type(instance.value) is type(expected)

    migration_db.reverse()
    assert TABLE_NAME not in connection.introspection.table_names()


def test_alter_max_length_and_reverse_preserves_rows(migration_db):
    record = migration_db.create(TypedField(models.CharField(max_length=8)))
    pk = record.objects.create(value="Nova").pk

    record = migration_db.apply(
        migrations.AlterField(
            "record",
            "value",
            TypedField(models.CharField(max_length=40)),
        )
    )

    assert record.objects.get(pk=pk).value == "Nova"
    assert record._meta.get_field("value").max_length == 40

    if connection.vendor == "postgresql":
        assert column_info("value").display_size == 40

    long_row = record.objects.create(value="A longer migration value")
    long_row.refresh_from_db()
    assert long_row.value == "A longer migration value"

    # Remove the long value before narrowing the column back to 8 characters.
    long_row.delete()

    migration_db.reverse()
    record = migration_db.model()

    assert record._meta.get_field("value").max_length == 8
    assert record.objects.get(pk=pk).value == "Nova"

    if connection.vendor == "postgresql":
        assert column_info("value").display_size == 8


def test_alter_nullability_and_reverse_enforces_database_constraint(migration_db):
    record = migration_db.create(TypedField(models.IntegerField()))
    pk = record.objects.create(value=42).pk
    record = migration_db.apply(
        migrations.AlterField("record", "value", TypedField(models.IntegerField(null=True)))
    )
    assert column_info("value").null_ok
    nullable_row = record.objects.create(value=None)
    nullable_row.refresh_from_db()
    assert nullable_row.value is None
    nullable_row.delete()  # Reverse to NOT NULL requires valid existing rows.

    migration_db.reverse()
    record = migration_db.model()
    assert not column_info("value").null_ok
    assert record.objects.get(pk=pk).value == 42
    with pytest.raises(IntegrityError), transaction.atomic():
        record.objects.create(value=None)


def test_rename_field_and_reverse_preserves_decimal(migration_db):
    record = migration_db.create(TypedField(models.DecimalField(max_digits=8, decimal_places=2)))
    pk = record.objects.create(value="12.50").pk
    record = migration_db.apply(migrations.RenameField("record", "value", "amount"))
    instance = record.objects.get(pk=pk)
    assert instance.amount == Decimal("12.50")
    assert isinstance(instance.amount, Decimal)
    assert column_info("amount").name == "amount"
    instance.amount = Decimal("23.75")
    instance.save(update_fields=["amount"])

    migration_db.reverse()
    instance = migration_db.model().objects.get(pk=pk)
    assert instance.value == Decimal("23.75")
    assert isinstance(instance.value, Decimal)
    assert column_info("value").name == "value"


def test_remove_database_default_and_reverse(migration_db):
    record = migration_db.create(TypedField(models.IntegerField(db_default=7)))
    table = connection.ops.quote_name(TABLE_NAME)
    with connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO {table} DEFAULT VALUES RETURNING id")
        pk = cursor.fetchone()[0]
    assert record.objects.get(pk=pk).value == 7

    field = TypedField(models.IntegerField(db_default=7))
    field.db_default = models.NOT_PROVIDED
    record = migration_db.apply(migrations.AlterField("record", "value", field))
    assert record.objects.get(pk=pk).value == 7
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO {table} DEFAULT VALUES")

    migration_db.reverse()
    with connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO {table} DEFAULT VALUES RETURNING id")
        restored_pk = cursor.fetchone()[0]
    assert migration_db.model().objects.get(pk=restored_pk).value == 7

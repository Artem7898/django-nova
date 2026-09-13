"""Database round trips for scalar TypedField values, without Nova validation."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from django.db import connection, models
from django.test.utils import isolate_apps

from nova.typing.fields import TypedField

CASES = [
    pytest.param(models.IntegerField(), "42", 42, id="integer"),
    pytest.param(models.BooleanField(), True, True, id="boolean"),
    pytest.param(models.CharField(max_length=30), "Nova", "Nova", id="text"),
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
    pytest.param(models.DateField(), "2026-09-13", date(2026, 9, 13), id="date"),
    pytest.param(
        models.DateTimeField(),
        datetime(2026, 9, 13, 12, tzinfo=UTC),
        datetime(2026, 9, 13, 12, tzinfo=UTC),
        id="datetime",
    ),
    pytest.param(models.TimeField(), "12:34:56", time(12, 34, 56), id="time"),
    pytest.param(
        models.DurationField(),
        timedelta(days=2, seconds=3),
        timedelta(days=2, seconds=3),
        id="duration",
    ),
    pytest.param(
        models.JSONField(),
        {"items": [1, True, None]},
        {"items": [1, True, None]},
        id="json-object",
    ),
    pytest.param(models.JSONField(), [1, "Nova"], [1, "Nova"], id="json-array"),
    pytest.param(models.JSONField(), "Nova", "Nova", id="json-string"),
    pytest.param(
        models.DecimalField(max_digits=8, decimal_places=2, null=True),
        None,
        None,
        id="nullable-decimal",
    ),
    pytest.param(models.JSONField(null=True), None, None, id="nullable-json"),
]


@pytest.fixture
def scalar_model(transactional_db, settings, inner):
    """Own the table explicitly; do not change the project's test migrations."""
    settings.USE_TZ = True
    with isolate_apps():

        class ScalarRoundTrip(models.Model):
            value = TypedField(inner.clone())

            class Meta:
                app_label = "typed_field_roundtrip"
                db_table = "nova_test_typed_field_roundtrip"

        with connection.schema_editor() as editor:
            editor.create_model(ScalarRoundTrip)
        try:
            yield ScalarRoundTrip
        finally:
            with connection.schema_editor() as editor:
                editor.delete_model(ScalarRoundTrip)


@pytest.mark.parametrize(("inner", "raw", "expected"), CASES)
def test_scalar_database_round_trip(scalar_model, raw, expected):
    # No full_clean(): exercise Django's real database preparation/conversion.
    instance = scalar_model.objects.create(value=raw)
    fetched = scalar_model.objects.get(pk=instance.pk)
    assert fetched.value == expected
    assert type(fetched.value) is type(expected)

    instance.refresh_from_db()
    assert instance.value == expected
    assert type(instance.value) is type(expected)

    projected = scalar_model.objects.values_list("value", flat=True).get(pk=instance.pk)
    assert projected == expected
    assert type(projected) is type(expected)

    # Save the converted value again, then force another database read.
    instance.save(update_fields=["value"])
    instance.refresh_from_db()
    assert instance.value == expected
    assert type(instance.value) is type(expected)

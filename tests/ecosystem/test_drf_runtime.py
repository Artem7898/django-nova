"""Real serializer/ORM contracts, shared by SQLite and PostgreSQL runs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Self

import pytest

pytest.importorskip("rest_framework", reason="DRF integration requires the drf extra")

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.storage import InMemoryStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models
from django.test import override_settings
from pydantic import BaseModel, ConfigDict, Field, model_validator
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APIRequestFactory
from rest_framework.viewsets import ModelViewSet

from nova import NovaConfig, NovaModel
from nova.core.exceptions import NovaValidationError
from nova.ecosystem.drf import to_drf_serializer


class InvoiceSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3)
    amount: Decimal = Field(gt=0)
    currency: Literal["USD", "EUR"]
    lower: int
    upper: int
    note: str | None = None
    metadata: dict[str, int] = Field(default_factory=dict)
    status: Literal["draft", "paid"] = "draft"
    created_at: datetime | None = None
    internal_code: str = "server"

    @model_validator(mode="after")
    def check_range(self) -> Self:
        if self.lower > self.upper:
            raise ValueError("lower must not exceed upper")
        return self


class DrfInvoice(NovaModel):
    title = models.CharField(max_length=80, unique=True)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    lower = models.IntegerField(default=1)
    upper = models.IntegerField(default=10)
    note = models.CharField(max_length=100, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=10, choices=[("draft", "Draft"), ("paid", "Paid")], default="draft"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    internal_code = models.CharField(max_length=20, default="server", editable=False)
    secret_note = models.TextField(default="private", blank=True)

    _nova_config = NovaConfig(pydantic_schema=InvoiceSchema)

    class Meta:
        app_label = "tests"

    def clean(self):
        if self.title == "blocked by Django":
            raise DjangoValidationError({"title": "Django clean rejected this title"})


class OwnerSchema(BaseModel):
    id: int | None = None
    name: str


class DrfOwner(NovaModel):
    name = models.CharField(max_length=80, unique=True)
    _nova_config = NovaConfig(pydantic_schema=OwnerSchema)

    class Meta:
        app_label = "tests"


class LinkedSchema(BaseModel):
    title: str
    owner: int


class DrfLinkedRecord(NovaModel):
    title = models.CharField(max_length=80)
    owner = models.ForeignKey(DrfOwner, on_delete=models.CASCADE, related_name="linked")
    _nova_config = NovaConfig(pydantic_schema=LinkedSchema)

    class Meta:
        app_label = "tests"


class LinkedNameSchema(BaseModel):
    title: str
    owner: str


class DrfLinkedByNameRecord(NovaModel):
    title = models.CharField(max_length=80)
    owner = models.ForeignKey(DrfOwner, to_field="name", on_delete=models.CASCADE)
    _nova_config = NovaConfig(pydantic_schema=LinkedNameSchema)

    class Meta:
        app_label = "tests"


class NestedSchema(BaseModel):
    title: str
    owner: OwnerSchema | None = None


class DrfNestedRecord(NovaModel):
    title = models.CharField(max_length=80)
    owner = models.ForeignKey(
        DrfOwner, on_delete=models.SET_NULL, null=True, blank=True, related_name="nested"
    )
    _nova_config = NovaConfig(pydantic_schema=NestedSchema)

    class Meta:
        app_label = "tests"


class FileSchema(BaseModel):
    label: str
    file: str


class DrfFileRecord(NovaModel):
    label = models.CharField(max_length=80)
    file = models.FileField(storage=InMemoryStorage(), upload_to="drf-contracts/")
    _nova_config = NovaConfig(pydantic_schema=FileSchema)

    class Meta:
        app_label = "tests"


pytestmark = pytest.mark.django_db


@pytest.fixture
def serializer_cls():
    return to_drf_serializer(DrfInvoice)


@pytest.fixture
def payload():
    return {
        "title": "Valid invoice",
        "amount": "12.50",
        "currency": "USD",
        "lower": 1,
        "upper": 10,
    }


@pytest.fixture
def invoice(payload):
    return DrfInvoice.objects.create(**payload)


def assert_unchanged(instance, before):
    instance.refresh_from_db()
    assert DrfInvoice.objects.filter(pk=instance.pk).values().get() == before
    assert DrfInvoice.objects.count() == 1


def test_create_validates_without_writes_then_persists(serializer_cls, payload):
    serializer = serializer_cls(data=payload)
    assert serializer.is_valid(), serializer.errors
    assert DrfInvoice.objects.count() == 0
    assert isinstance(serializer.validated_data["amount"], Decimal)
    result = serializer.save()
    result.refresh_from_db()
    assert result.amount == Decimal("12.50")
    assert result.title == payload["title"]
    assert serializer.data["id"] == result.pk
    assert serializer.data["amount"] == "12.50"
    assert DrfInvoice.objects.count() == 1


def test_create_applies_django_defaults_before_pydantic_validation(serializer_cls):
    serializer = serializer_cls(data={"title": "Defaults", "amount": "5.00"})
    assert serializer.is_valid(), serializer.errors
    result = serializer.save()
    result.refresh_from_db()
    assert (result.currency, result.lower, result.upper) == ("USD", 1, 10)
    assert result.metadata == {}
    assert result.status == "draft"


def test_callable_default_is_evaluated_once_and_reused_on_save(monkeypatch, serializer_cls):
    calls = []

    def next_value():
        calls.append(len(calls) + 1)
        return calls[-1]

    field = DrfInvoice._meta.get_field("lower")
    monkeypatch.setattr(field, "default", next_value)
    monkeypatch.delitem(field.__dict__, "_get_default", raising=False)
    serializer = serializer_cls(data={"title": "Factory", "amount": "5.00"})
    assert calls == []
    assert serializer.is_valid(), serializer.errors
    assert serializer.is_valid()
    result = serializer.save()
    result.refresh_from_db()
    assert calls == [1]
    assert result.lower == 1


def test_invalid_django_default_is_rejected_before_save(monkeypatch, serializer_cls):
    field = DrfInvoice._meta.get_field("currency")
    monkeypatch.setattr(field, "default", "GBP")
    monkeypatch.delitem(field.__dict__, "_get_default", raising=False)
    serializer = serializer_cls(data={"title": "Bad default", "amount": "5"})
    assert not serializer.is_valid()
    assert "currency" in serializer.errors
    assert DrfInvoice.objects.count() == 0


@pytest.mark.parametrize("partial", [False, True])
def test_updates_do_not_evaluate_or_reset_omitted_defaults(
    monkeypatch, serializer_cls, invoice, payload, partial
):
    def unexpected_default():
        raise AssertionError("An update must not evaluate a creation default")

    field = DrfInvoice._meta.get_field("lower")
    monkeypatch.setattr(field, "default", unexpected_default)
    monkeypatch.delitem(field.__dict__, "_get_default", raising=False)
    data = {"upper": 20} if partial else {**payload, "upper": 20}
    data.pop("lower", None)
    serializer = serializer_cls(invoice, data=data, partial=partial)
    assert serializer.is_valid(), serializer.errors
    assert "lower" not in serializer.validated_data
    serializer.save()
    invoice.refresh_from_db()
    assert invoice.lower == 1


def test_full_update_persists_supplied_fields(serializer_cls, invoice, payload):
    serializer = serializer_cls(invoice, data={**payload, "title": "Updated", "amount": "22"})
    assert serializer.is_valid(), serializer.errors
    result = serializer.save()
    assert result is invoice
    result.refresh_from_db()
    assert result.title == "Updated"
    assert result.amount == Decimal("22.00")
    assert DrfInvoice.objects.count() == 1


def test_partial_update_uses_current_state_and_preserves_omitted_fields(serializer_cls, invoice):
    serializer = serializer_cls(invoice, data={"upper": 20}, partial=True)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"upper": 20}
    result = serializer.save()
    result.refresh_from_db()
    assert result.upper == 20
    assert result.lower == 1
    assert result.amount == Decimal("12.50")
    assert result.currency == "USD"


def test_empty_partial_update_keeps_state(serializer_cls, invoice):
    before = DrfInvoice.objects.values().get(pk=invoice.pk)
    serializer = serializer_cls(invoice, data={}, partial=True)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {}
    serializer.save()
    assert_unchanged(invoice, before)


@pytest.mark.parametrize("partial", [False, True])
def test_cross_field_error_does_not_mutate_instance_or_database(
    serializer_cls, invoice, payload, partial
):
    before = DrfInvoice.objects.values().get(pk=invoice.pk)
    data = {"lower": 20} if partial else {**payload, "lower": 20}
    serializer = serializer_cls(invoice, data=data, partial=partial)
    assert not serializer.is_valid()
    assert "lower must not exceed upper" in str(serializer.errors["non_field_errors"])
    assert invoice.lower == 1
    assert_unchanged(invoice, before)


def test_custom_non_field_errors_key_is_respected(serializer_cls, payload):
    with override_settings(REST_FRAMEWORK={"NON_FIELD_ERRORS_KEY": "detail"}):
        serializer = serializer_cls(data={**payload, "lower": 20})
        assert not serializer.is_valid()
        assert set(serializer.errors) == {"detail"}
    assert DrfInvoice.objects.count() == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "x"),
        ("title", "x" * 81),
        ("title", ""),
        ("title", None),
        ("amount", "0"),
        ("amount", "-1"),
        ("amount", "not-a-decimal"),
        ("amount", "1.234"),
        ("status", "unknown"),
        ("currency", "GBP"),
        ("metadata", {"count": "not-an-int"}),
    ],
)
def test_invalid_create_returns_field_errors_without_persistence(
    serializer_cls, payload, field, value
):
    serializer = serializer_cls(data={**payload, field: value})
    with pytest.raises(DRFValidationError):
        serializer.is_valid(raise_exception=True)
    assert field in serializer.errors
    assert DrfInvoice.objects.count() == 0


@pytest.mark.parametrize("partial", [False, True])
def test_partial_flag_does_not_make_invalid_values_acceptable(
    serializer_cls, invoice, payload, partial
):
    before = DrfInvoice.objects.values().get(pk=invoice.pk)
    data = {"amount": "-1"} if partial else {**payload, "amount": "-1"}
    serializer = serializer_cls(invoice, data=data, partial=partial)
    assert not serializer.is_valid()
    assert "amount" in serializer.errors
    assert_unchanged(invoice, before)


def test_full_update_still_requires_required_transport_fields(serializer_cls, invoice):
    serializer = serializer_cls(invoice, data={"upper": 20})
    assert not serializer.is_valid()
    assert {"title", "amount"} <= serializer.errors.keys()


@pytest.mark.parametrize("value", [None, ""])
def test_nullable_and_blank_field_roundtrips(serializer_cls, invoice, value):
    serializer = serializer_cls(invoice, data={"note": value}, partial=True)
    assert serializer.is_valid(), serializer.errors
    serializer.save()
    invoice.refresh_from_db()
    assert invoice.note == value


def test_json_roundtrip_and_representation_exclude_persistence_only_fields(serializer_cls, payload):
    serializer = serializer_cls(data={**payload, "metadata": {"count": 7}, "secret_note": "attack"})
    assert serializer.is_valid(), serializer.errors
    result = serializer.save()
    result.refresh_from_db()
    assert result.metadata == {"count": 7}
    assert result.secret_note == "private"
    assert "secret_note" not in serializer.data
    assert "secret_note" not in serializer.validated_data


def test_read_only_fields_ignore_client_values(serializer_cls, invoice):
    created_at = invoice.created_at
    pk = invoice.pk
    serializer = serializer_cls(
        invoice,
        data={"id": pk + 100, "internal_code": "client", "created_at": "1990-01-01T00:00:00Z"},
        partial=True,
    )
    assert all(serializer.fields[name].read_only for name in ("id", "created_at", "internal_code"))
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {}
    serializer.save()
    invoice.refresh_from_db()
    assert invoice.pk == pk
    assert invoice.internal_code == "server"
    assert invoice.created_at == created_at


def test_unique_field_rejects_duplicate_and_accepts_same_instance(serializer_cls, invoice, payload):
    duplicate = serializer_cls(data=payload)
    assert not duplicate.is_valid()
    assert "title" in duplicate.errors
    current = serializer_cls(invoice, data={"title": invoice.title}, partial=True)
    assert current.is_valid(), current.errors
    current.save()
    assert DrfInvoice.objects.count() == 1


def test_patch_can_repair_invalid_persisted_state(serializer_cls, invoice):
    # QuerySet.update deliberately bypasses the authoritative save validation.
    DrfInvoice.objects.filter(pk=invoice.pk).update(amount=-1)
    invoice.refresh_from_db()
    serializer = serializer_cls(invoice, data={"amount": "4.00"}, partial=True)
    assert serializer.is_valid(), serializer.errors
    serializer.save()
    invoice.refresh_from_db()
    assert invoice.amount == Decimal("4.00")


@pytest.mark.parametrize("operation", ["create", "update"])
def test_model_clean_remains_authoritative_without_database_changes(
    serializer_cls, payload, operation
):
    instance = DrfInvoice.objects.create(**payload) if operation == "update" else None
    before = list(DrfInvoice.objects.values())
    serializer = serializer_cls(instance, data={**payload, "title": "blocked by Django"})
    assert serializer.is_valid(), serializer.errors
    with pytest.raises(NovaValidationError, match="Django model validation failed"):
        serializer.save()
    assert list(DrfInvoice.objects.values()) == before


@pytest.mark.parametrize("operation", ["create", "update"])
def test_save_kwargs_cannot_bypass_orm_validation(serializer_cls, payload, operation):
    instance = DrfInvoice.objects.create(**payload) if operation == "update" else None
    before = list(DrfInvoice.objects.values())
    serializer = serializer_cls(instance, data=payload)
    assert serializer.is_valid(), serializer.errors
    with pytest.raises(NovaValidationError):
        serializer.save(amount=Decimal("-1"))
    assert list(DrfInvoice.objects.values()) == before


def test_many_validates_all_items_before_saving(serializer_cls, payload):
    serializer = serializer_cls(
        data=[payload, {**payload, "title": "Invalid item", "amount": "-1"}], many=True
    )
    assert not serializer.is_valid()
    assert serializer.errors[0] == {}
    assert "amount" in serializer.errors[1]
    assert DrfInvoice.objects.count() == 0


def test_many_create_and_serialize(serializer_cls, payload):
    serializer = serializer_cls(data=[payload, {**payload, "title": "Second item"}], many=True)
    assert serializer.is_valid(), serializer.errors
    records = serializer.save()
    assert len(records) == DrfInvoice.objects.count() == 2
    assert [row["title"] for row in serializer_cls(records, many=True).data] == [
        "Valid invoice",
        "Second item",
    ]


@pytest.mark.parametrize(
    "model", [DrfLinkedRecord, DrfNestedRecord], ids=["pk-schema", "nested-schema"]
)
def test_foreign_key_create_and_partial_update_keep_django_objects_for_save(model):
    first = DrfOwner.objects.create(name="First")
    second = DrfOwner.objects.create(name="Second")
    serializer_cls = to_drf_serializer(model)
    serializer = serializer_cls(data={"title": "Related", "owner": first.pk})
    assert serializer.is_valid(), serializer.errors
    assert isinstance(serializer.validated_data["owner"], DrfOwner)
    result = serializer.save()
    result.refresh_from_db()
    assert result.owner_id == first.pk
    update = serializer_cls(result, data={"owner": second.pk}, partial=True)
    assert update.is_valid(), update.errors
    update.save()
    result.refresh_from_db()
    assert result.owner_id == second.pk
    assert update.data["owner"] == second.pk
    assert DrfOwner.objects.count() == 2


def test_nullable_foreign_key_can_be_cleared():
    owner = DrfOwner.objects.create(name="Owner")
    record = DrfNestedRecord.objects.create(title="Related", owner=owner)
    serializer = to_drf_serializer(DrfNestedRecord)(record, data={"owner": None}, partial=True)
    assert serializer.is_valid(), serializer.errors
    serializer.save()
    record.refresh_from_db()
    assert record.owner_id is None


def test_invalid_foreign_key_does_not_create_record():
    serializer = to_drf_serializer(DrfLinkedRecord)(data={"title": "Related", "owner": 99999})
    assert not serializer.is_valid()
    assert "owner" in serializer.errors
    assert DrfLinkedRecord.objects.count() == 0


def test_foreign_key_to_field_validates_the_referenced_value():
    owner = DrfOwner.objects.create(name="external-key")
    serializer = to_drf_serializer(DrfLinkedByNameRecord)(
        data={"title": "Natural key", "owner": owner.name}
    )
    assert serializer.is_valid(), serializer.errors
    assert isinstance(serializer.validated_data["owner"], DrfOwner)
    record = serializer.save()
    record.refresh_from_db()
    assert record.owner_id == "external-key"
    assert record.owner.pk == owner.pk


def test_primary_key_already_declared_by_schema_is_not_duplicated():
    serializer_cls = to_drf_serializer(DrfOwner)
    assert serializer_cls.Meta.fields == ["id", "name"]
    serializer = serializer_cls(data={"id": 12345, "name": "Owner"})
    assert serializer.fields["id"].read_only
    assert serializer.is_valid(), serializer.errors
    result = serializer.save()
    assert result.pk != 12345
    assert serializer.data == {"id": result.pk, "name": "Owner"}


def test_uploaded_file_is_validated_by_name_and_saved_as_file():
    upload = SimpleUploadedFile("payload.txt", b"x" * 4096, content_type="text/plain")
    serializer = to_drf_serializer(DrfFileRecord)(data={"label": "File", "file": upload})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["file"] is upload
    record = serializer.save()
    try:
        record.refresh_from_db()
        assert record.file.name.endswith("payload.txt")
        assert record.file.size == 4096
        with record.file.open("rb") as stored:
            assert stored.read() == b"x" * 4096
        update = to_drf_serializer(DrfFileRecord)(record, data={"label": "Updated"}, partial=True)
        assert update.is_valid(), update.errors
        update.save()
        record.refresh_from_db()
        assert record.label == "Updated"
        assert record.file.size == 4096
    finally:
        record.file.delete(save=False)


def test_viewset_create_and_patch_use_the_generated_serializer(serializer_cls):
    class InvoiceViewSet(ModelViewSet):
        queryset = DrfInvoice.objects.all()
        serializer_class = serializer_cls

    factory = APIRequestFactory()
    created = InvoiceViewSet.as_view({"post": "create"})(
        factory.post("/invoices/", {"title": "API invoice", "amount": "3.25"}, format="json")
    )
    assert created.status_code == 201, created.data
    created.render()
    pk = created.data["id"]
    updated = InvoiceViewSet.as_view({"patch": "partial_update"})(
        factory.patch(f"/invoices/{pk}/", {"upper": 30}, format="json"), pk=pk
    )
    assert updated.status_code == 200, updated.data
    record = DrfInvoice.objects.get(pk=pk)
    assert record.upper == 30
    assert record.lower == 1
    assert record.amount == Decimal("3.25")


def test_viewset_returns_400_for_invalid_patch_without_persistence(serializer_cls, invoice):
    class InvoiceViewSet(ModelViewSet):
        queryset = DrfInvoice.objects.all()
        serializer_class = serializer_cls

    before = DrfInvoice.objects.values().get(pk=invoice.pk)
    request = APIRequestFactory().patch("/invoices/", {"lower": 100}, format="json")
    response = InvoiceViewSet.as_view({"patch": "partial_update"})(request, pk=invoice.pk)
    assert response.status_code == 400
    assert "non_field_errors" in response.data
    assert_unchanged(invoice, before)

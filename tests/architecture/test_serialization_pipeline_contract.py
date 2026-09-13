"""Integration contracts for the Nova serialization pipeline."""

from __future__ import annotations

import pytest
from django.core.files.base import ContentFile
from django.db import models
from django.test.utils import isolate_apps
from pydantic import BaseModel, ConfigDict

from nova import NovaConfig, NovaModel


class PublicSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    title: str


@pytest.fixture
def pipeline_models():
    with isolate_apps():

        class Label(NovaModel):
            name = models.CharField(max_length=50)

            class Meta:
                app_label = "serialization_pipeline_tests"

        class Entry(NovaModel):
            title = models.CharField(max_length=100)
            secret = models.TextField(default="")
            label = models.ForeignKey(
                Label,
                null=True,
                on_delete=models.SET_NULL,
            )
            labels = models.ManyToManyField(Label)

            _nova_config = NovaConfig(
                exclude_from_pydantic=("secret",),
                cache_enabled=False,
            )

            class Meta:
                app_label = "serialization_pipeline_tests"

        class Document(NovaModel):
            attachment = models.FileField(
                upload_to="documents/",
                max_length=100,
            )

            _nova_config = NovaConfig(
                cache_enabled=False,
            )

            class Meta:
                app_label = "serialization_pipeline_tests"

        yield Entry, Document


def test_unsaved_m2m_is_safe_with_generated_schema(
    pipeline_models,
) -> None:
    entry_model, _ = pipeline_models
    entry = entry_model(title="Nova", secret="private")

    result = entry.to_pydantic()

    assert entry.pk is None
    assert result.model_dump() == {
        "id": None,
        "title": "Nova",
    }


def test_generated_schema_excludes_configured_field(
    pipeline_models,
) -> None:
    from nova.validation.pydantic_bridge import generate_pydantic_schema

    entry_model, _ = pipeline_models
    entry = entry_model(title="Nova", secret="private")

    schema = generate_pydantic_schema(model_cls=entry_model)
    result = entry.to_pydantic()

    assert "secret" not in schema.model_fields
    assert "secret" not in result.model_dump()
    assert entry.secret == "private"


def test_explicit_schema_does_not_read_excluded_attributes(
    pipeline_models,
    monkeypatch,
) -> None:
    entry_model, _ = pipeline_models
    entry = entry_model(title="Nova", secret="private")

    monkeypatch.setattr(
        entry_model,
        "_nova_config",
        NovaConfig(
            pydantic_schema=PublicSchema,
            cache_enabled=False,
        ),
    )

    def forbidden_read(_instance):
        raise AssertionError("Excluded attribute was accessed")

    # Install traps after constructing the instance.
    for name in ("secret", "label", "label_id", "labels"):
        monkeypatch.setattr(
            entry_model,
            name,
            property(forbidden_read),
        )

    result = entry.to_pydantic()

    assert isinstance(result, PublicSchema)
    assert result.model_dump() == {
        "id": None,
        "title": "Nova",
    }


def test_large_file_passes_generated_schema(
    pipeline_models,
) -> None:
    _, document_model = pipeline_models
    document = document_model()
    document.attachment = ContentFile(
        b"x" * 4096,
        name="report.txt",
    )

    assert document.attachment.size == 4096

    result = document.to_pydantic()

    assert result.model_dump() == {
        "id": None,
        "attachment": "report.txt",
    }
    assert isinstance(result.attachment, str)


def test_serialization_does_not_read_file_content(
    pipeline_models,
) -> None:
    _, document_model = pipeline_models

    class UnreadableContentFile(ContentFile):
        def read(self, *args, **kwargs):
            raise AssertionError("File content must not be read")

    document = document_model()
    document.attachment = UnreadableContentFile(
        b"private content",
        name="private.txt",
    )

    result = document.to_pydantic()

    assert result.attachment == "private.txt"

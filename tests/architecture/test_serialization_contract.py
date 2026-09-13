"""Contract tests for schema-whitelisted model serialization."""

from __future__ import annotations

from django.core.files.base import ContentFile
from django.db import models
from django.db.models.fields.files import FieldFile
from django.test.utils import isolate_apps
from pydantic import BaseModel, ConfigDict

from tests.models import Article, GrantWithSecret


class ArticleProjectionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    title: str


class GrantProjectionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    title: str


class FileProjectionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    attachment: str


def test_serialization_is_schema_whitelisted() -> None:
    from nova.validation.serialization import model_to_dict

    article = Article(
        title="Nova",
        body="Article body",
    )

    data = model_to_dict(
        article,
        schema_cls=ArticleProjectionSchema,
    )

    assert data == {
        "id": None,
        "title": "Nova",
    }
    ArticleProjectionSchema.model_validate(data)


def test_excluded_model_fields_are_not_serialized() -> None:
    from nova.validation.serialization import model_to_dict

    grant = GrantWithSecret(
        title="Demo grant",
        secret_note="private",
    )

    data = model_to_dict(
        grant,
        schema_cls=GrantProjectionSchema,
    )

    assert data == {
        "id": None,
        "title": "Demo grant",
    }
    assert grant.secret_note == "private"
    GrantProjectionSchema.model_validate(data)


@isolate_apps()
def test_file_field_is_serialized_as_string() -> None:
    from nova import NovaModel
    from nova.validation.serialization import model_to_dict

    class Document(NovaModel):
        attachment = models.FileField(
            upload_to="documents/",
            max_length=100,
        )

        class Meta:
            app_label = "serialization_contract_tests"

    document = Document()
    document.attachment = ContentFile(
        b"x" * 1024,
        name="report.txt",
    )

    assert isinstance(document.attachment, FieldFile)

    data = model_to_dict(
        document,
        schema_cls=FileProjectionSchema,
    )

    assert data == {
        "id": None,
        "attachment": "report.txt",
    }
    FileProjectionSchema.model_validate(data)

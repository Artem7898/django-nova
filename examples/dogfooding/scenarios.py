"""Executable checks; failures terminate the demo with a non-zero exit code."""

import asyncio
from decimal import Decimal

from django.core.files.base import ContentFile

from nova import NovaManager, TypedField, TypedQuerySet, get_default_cache, nova_task
from nova.core.exceptions import NovaValidationError
from nova.core.tracing import nova_span
from nova.tasks.engine import get_engine
from nova.typing import NovaManager as CanonicalManager
from nova.typing import TypedField as CanonicalField
from nova.typing import TypedQuerySet as CanonicalQuerySet
from nova.validation.pydantic_bridge import generate_pydantic_schema

from .models import Article, Label, Project, RelaxedArticle


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def models_demo() -> None:
    require(NovaManager is CanonicalManager, "NovaManager export differs")
    require(TypedField is CanonicalField, "TypedField export differs")
    require(TypedQuerySet is CanonicalQuerySet, "TypedQuerySet export differs")
    require(get_default_cache() is get_default_cache(), "Default cache is not shared")
    print("OK public exports")

    project = Project(slug="nova-demo", budget="12.50", internal_note="private")
    project.report = ContentFile(b"x" * 4096, name="report.txt")
    payload = project.to_dict()
    require(project.pk is None, "Expected an unsaved project")
    require("labels" not in payload, "Scalar serialization included M2M")
    require("internal_note" not in payload, "Excluded field was serialized")
    require(payload["report"] == "report.txt", "File was not serialized by name")
    schema = generate_pydantic_schema(Project)
    require(not schema.model_fields["created_at"].is_required(), "Generated date is required")
    require(project.to_pydantic().created_at is None, "Unsaved date should be empty")
    print("OK unsaved serialization, generated date and file name")

    with nova_span("dogfooding.project.save"):
        project.save()
    require(isinstance(project.budget, Decimal), "Save did not normalize Decimal")
    require(project.budget == Decimal("12.50"), "Budget changed")
    require(project.created_at is not None, "Save did not populate the date")
    require(Project.objects.get(pk=project.pk).budget == Decimal("12.50"), "DB round trip")
    with project.report.open("rb") as uploaded:
        require(len(uploaded.read()) == 4096, "File content changed")
    require(project.to_dict()["report"] == "reports/report.txt", "Stored file name")
    label = Label.objects.create(name="demo")
    project.labels.add(label)
    require(list(project.labels.values_list("name", flat=True)) == ["demo"], "M2M save")
    require("labels" not in project.to_dict(), "M2M unexpectedly entered scalar payload")
    print("OK migrated database, Decimal normalization, 4096-byte file and saved M2M")

    try:
        Project(slug="negative", budget="-1.00").save()
    except NovaValidationError as exc:
        require("Budget must be non-negative" in str(exc.details), "Expected Model.clean error")
    else:
        raise AssertionError("Negative budget was saved")
    require(Project.objects.count() == 1, "Invalid project reached the database")
    print("OK Model.clean rejects a negative Decimal before persistence")

    try:
        Article(title="Hi").save()
    except NovaValidationError:
        pass
    else:
        raise AssertionError("Strict article bypassed the Pydantic rule")
    require(Article.objects.count() == 0, "Invalid strict article was persisted")
    RelaxedArticle(title="Hi").save()
    try:
        RelaxedArticle(title="").save()
    except NovaValidationError:
        pass
    else:
        raise AssertionError("Relaxed article bypassed Django blank validation")
    require(Article.objects.count() == 1, "Invalid relaxed article was persisted")
    print("OK strict_validation=False skips Pydantic and retains Django validation")


async def tasks_demo() -> None:
    engine = get_engine()
    release = asyncio.Event()
    completed = asyncio.Event()

    @nova_task(name="dogfooding.complete")
    async def complete() -> str:
        await release.wait()
        completed.set()
        return "business-result"

    await engine.start()
    try:
        task_id = await complete()
        require(isinstance(task_id, str) and bool(task_id), "Submission did not return an ID")
        require(not completed.is_set(), "Submission waited for business completion")
        release.set()
        await asyncio.wait_for(completed.wait(), timeout=5)
        status = engine.get_status(task_id)
        require(status is not None and status.result == "business-result", "Task result missing")
        print("OK awaited engine lifecycle; decorated call returns a task ID")
    finally:
        await engine.stop()

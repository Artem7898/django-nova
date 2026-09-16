<div align="center">
  <img src="assets/django-nova-logo.png" width="220" alt="Django Nova logo">
</div>

# Django Nova

**Typed Django models. Shared Pydantic schemas. Explicit validation boundaries.**

Django Nova is a typed, async-oriented toolkit for Django 5. It connects Django
models with Pydantic schemas, schema-driven query planning, caching, background
tasks, and optional OpenTelemetry tracing.

[PyPI](https://pypi.org/project/django-nova/) ·
[Documentation](docs/index.md) ·
[Project site](https://artem7898.github.io/django-nova-site/) ·
[Coverage report](STATUS.md) ·
[Roadmap](ROADMAP.md)

[![Coverage](https://img.shields.io/badge/coverage-87%-green.svg)](STATUS.md)

> **Development status: Beta.** This README describes the current development
> workflow and the reviewed source implementation. Recent source changes may not
> yet be included in the published PyPI package. Check the
> [changelog](CHANGELOG.md) and the version you install before adopting an API.

## Philosophy

Nova keeps Django's ORM and database semantics while making the boundaries around
validation, serialization, and typing explicit.

- **Shared schemas:** use Pydantic to express data contracts and reusable rules.
  Django fields, `Model.clean()`, uniqueness checks, and database constraints retain
  their own responsibilities.
- **Typed boundaries:** contain dynamic Django metadata behind focused helpers.
  Static analysis complements runtime validation; it cannot replace it.
- **Schema-selected serialization:** serialize the fields required by the selected
  schema rather than exposing every model attribute.
- **Explicit async behavior:** support asynchronous workflows without implying that
  every operation or relation lookup is non-blocking.
- **Reproducible evidence:** report tests, coverage, and benchmarks as measurements,
  not as automatic proof of production readiness.

## Requirements

The current [package manifest](pyproject.toml) declares:

| Dependency | Requirement |
|---|---|
| Python | 3.12 or newer |
| Django | `>=5.0,<6.0` |
| Pydantic | `>=2.8,<3.0` |

These are dependency bounds, not a claim that every version combination has been
tested. Django 6 is outside the declared range.

## Installation

For an existing project managed with uv:

```bash
uv add django-nova
```

Or with pip inside an activated virtual environment:

```bash
python -m pip install django-nova
```

Optional integrations are installed explicitly:

```bash
uv add 'django-nova[redis]'
uv add 'django-nova[drf]'
uv add 'django-nova[fastapi]'
uv add 'django-nova[graphql]'
uv add 'django-nova[tracing]'
uv add 'django-nova[observability]'
```

Choose the extras your application needs. `tracing` supplies the OpenTelemetry
API; `observability` additionally supplies SDK/exporter dependencies. Installing
those dependencies does not configure a telemetry exporter. The manifest also
provides `cache`, `tasks`, and `async` extras; installing an extra alone does not
activate an integration or turn synchronous ORM calls into async operations.

Add Nova and your application to Django settings:

```python
# config/settings.py — extend your existing INSTALLED_APPS.
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "nova",
    "articles",
]
```

Keep the other Django applications required by your project. Import model classes
only after Django settings and the app registry are initialized.

## Quick start

The examples below assume an existing Django project with an `articles` app.
Create it if needed:

```bash
uv run python manage.py startapp articles
```

### 1. Define a schema and a model

```python
# articles/models.py
from django.db import models
from pydantic import BaseModel, Field

from nova import NovaConfig, NovaModel


class ArticleSchema(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    body: str
    views: int = Field(default=0, ge=0)


class Article(NovaModel):
    title = models.CharField(max_length=200)
    body = models.TextField()
    views = models.IntegerField(default=0)

    _nova_config = NovaConfig(
        pydantic_schema=ArticleSchema,
        strict_validation=True,
        cache_enabled=False,
    )
```

Django field declarations use assignment (`=`). Pydantic defines the application
contract; Django fields define persistence and field-level rules. Some limits
belong at both boundaries. An explicit schema does not rewrite the database model.

### 2. Create the database table

```bash
uv run python manage.py makemigrations articles
uv run python manage.py migrate
uv run python manage.py check
uv run python manage.py shell
```

### 3. Save and serialize

Run in the Django shell or an initialized application service:

```python
from articles.models import Article

article = Article(
    title="Hello Nova",
    body="Shared validation with explicit boundaries.",
)
article.save()

payload = article.to_pydantic().model_dump()
assert payload == {
    "title": "Hello Nova",
    "body": "Shared validation with explicit boundaries.",
    "views": 0,
}
assert "id" not in payload  # The explicit schema does not declare it.
```

### 4. Handle validation errors

```python
from articles.models import Article
from nova.core.exceptions import NovaValidationError

try:
    Article(title="Hi", body="Too short a title.").save()
except NovaValidationError as exc:
    print(f"Validation failed: {exc}")
```

For this example, the title fails Pydantic validation before persistence.

## Validation contract

`NovaModel.save()` runs this pipeline before calling Django's save implementation:

```text
Pydantic → Django field validation and conversion → Model.clean()
         → uniqueness checks → constraint checks → database save
```

- `strict_validation=True` enables the Pydantic stage.
- `strict_validation=False` skips that stage; Django validation still runs.
- Successful `field.clean()` results are assigned back to model attributes before
  `Model.clean()` executes.
- Full-model validation is retained when `save(update_fields=...)` is used.
- `QuerySet.update()`, `bulk_create()`, and `bulk_update()` bypass model `save()`;
  they do not acquire its validation contract.
- Database constraints remain necessary. Pre-save checks do not replace database
  enforcement under concurrent writes.

Automatic schema generation supports mapped Django types, inherited field types,
nullable fields, and callable defaults. Decimal digit constraints are transferred
to generated schemas. An unknown field falls back to `Any`, which does not provide
specialized type validation.

`FileField` and `ImageField` values are serialized as names/paths rather than file
contents. Their Django `max_length` is not transferred to the Pydantic value in the
current compiler. Django field validation still applies on the save path.

## Query planning and async ORM

For a Nova queryset, `.auto()` applies a plan derived from the model's schema:

```python
from articles.models import Article

queryset = Article.objects.filter(title__icontains="Nova").auto()
articles = list(queryset)
```

The selected joins, prefetches, and deferred fields depend on the model/schema.
Measure query counts for your actual workload; `.auto()` is not a universal
performance guarantee.

For Nova's additional async queryset helpers, explicitly configure the manager:

```python
from nova.async_orm import AsyncNovaManager

# Inside a NovaModel subclass:
# objects = AsyncNovaManager()
```

Django's async save API can be used from an async application function:

```python
from articles.models import Article


async def create_article() -> Article:
    article = Article(title="Async Nova", body="Created asynchronously.")
    await article.asave()
    return article
```

An unloaded nested foreign key may require synchronous SQL during serialization.
Do not assume that calling `to_pydantic()` directly in an async context is safe
for every model. Nested M2M and reverse-relation serialization remain separate
contracts; see the [roadmap](ROADMAP.md).

## Background tasks

`@nova_task()` decorates an async function. Awaiting the decorated call submits
work and returns a task ID; it does not await the task's business result.
The decorator uses the shared engine returned by `get_engine()`.

```python
import asyncio

from nova import nova_task
from nova.tasks.engine import get_engine


async def main() -> None:
    engine = get_engine()
    completed = asyncio.Event()

    @nova_task(name="demo.greet")
    async def greet(person: str) -> None:
        print(f"Hello, {person}!")
        completed.set()

    await engine.start()
    try:
        task_id = await greet("Nova")
        print(f"Submitted task: {task_id}")
        await asyncio.wait_for(completed.wait(), timeout=10)
    finally:
        await engine.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

This is an in-process example using the default backend. The event is demo-local
synchronization, not a distributed completion mechanism. Start the shared engine
in application startup and stop it during shutdown. Backend shutdown behavior,
retries, durability, and process recovery must be evaluated for your deployment.
Do not submit blocking CPU-intensive work directly to the event loop.

## Tracing

Tracing is optional. With no usable tracer, `nova_span()` yields `None`.
For async operations, keep the awaited work inside the span:

```python
from nova.core.tracing import nova_span


async def fetch_and_measure(fetch):
    with nova_span("article.fetch", component="service"):
        return await fetch()
```

The current tracing decorators support both ordinary functions and `async def`:

```python
from nova.core.tracing import trace_task


@trace_task(operation="calculate")
async def calculate_total(prices: list[int]) -> int:
    return sum(prices)
```

Nova guards its internal telemetry setup, recording, and teardown calls against
ordinary exceptions while preserving business results, exceptions, and
cancellation. Direct span method calls made by application code are outside that
protection. Provider signals derived directly from `BaseException` are not
suppressed. Configure the OpenTelemetry SDK and exporter separately if you want
to collect spans.

## Development and verification

To work on the repository rather than the published package:

```bash
git clone https://github.com/Artem7898/django-nova.git
cd django-nova
uv sync --group dev --all-extras
```

For an existing checkout containing local changes, run the sync command there;
cloning the remote does not include unpublished local fixes. Commit and retain
`uv.lock` for reproducible development environments.

Run static checks and the complete test suite:

```bash
uv run pyright src/nova \
&& uv run ruff check . \
&& uv run pytest -q
```

Pyright uses the repository configuration, including its exclusions and scoped
exceptions. A clean run is not a claim that every module has unrestricted strict
typing coverage. Application model typing also depends on its own checker and
Django stub configuration.

Focused checks:

```bash
uv run pytest -q tests/architecture
uv run pytest -q tests/typing/test_django_boundary.py
uv run pytest -q \
  tests/core/test_tracing.py \
  tests/core/test_tracing_async.py \
  tests/core/test_tracing_resilience.py
```

Check collection when adding or moving tests:

```bash
uv run pytest --collect-only -q tests/typing
```

Place test functions in discoverable `test_*.py` modules, not package `__init__.py`
files.

### Coverage and generated status

```bash
uv run pytest -q --cov=src/nova --cov-report=xml:coverage.xml \
&& uv run python scripts/generate_status.py --write \
&& uv run python scripts/generate_status.py --check
```

Update the README badge using the same coverage snapshot:

```bash
uv run python scripts/update_badge.py --write \
&& uv run python scripts/update_badge.py --check
```

The badge is initially a link to the report, not an invented coverage percentage.
The updater replaces its value with the measured percentage from `coverage.xml`.

The latest local verification reported for the reviewed development changes was
**786 tests passed**, with clean Pyright and Ruff results; the coverage run and
status generation also succeeded. This is a historical development snapshot,
not a live CI result or a result independently reproduced for every installation.

`STATUS.md` distinguishes missing measurements (`N/A`) from measured `0%`.
Its overall percentage comes from the XML report's line counters. `--check`
compares the generated report with supplied inputs; it does not prove that the
coverage snapshot matches the latest source contents.

## Performance

Benchmark model construction and serialization separately:

```bash
uv run python scripts/bench.py
```

Treat this as a local diagnostic, not a universal speed claim. Review the current
benchmark script before publishing results; the roadmap tracks improvements to
its execution and measurement setup.

Django model construction, Pydantic validation, `to_pydantic()`, SQL execution,
and cache access perform different work. Report Python and dependency versions,
hardware, benchmark configuration, repetitions, variability, and raw results.
Small differences within measurement noise do not establish that Nova is faster
than Django or adds zero overhead.

## Scope and limitations

| Area | Boundary to evaluate |
|---|---|
| Cache | Backend behavior, invalidation coverage, consistency, and failure handling |
| Tasks | Process-local behavior of the default backend; no implied durable queue guarantee |
| DRF, FastAPI, GraphQL | Integration-specific contracts and dependency versions |
| TypedField | Migration reconstruction and complete conversion/validator delegation |
| Migrations | PostgreSQL operation and transaction requirements; no blanket lock-free guarantee |
| Validation | Distinct Pydantic, Django, and database responsibilities |

See [ROADMAP.md](ROADMAP.md) for planned work and [STATUS.md](STATUS.md) for
coverage measurements. Neither assigns production readiness from a percentage.

## Contributing

Include a minimal reproducer for defects and a regression test for behavior
changes. Keep compatibility boundaries explicit, run the checks above, and
update documentation when public behavior changes.

- [Issue tracker](https://github.com/Artem7898/django-nova/issues)
- [Changelog](CHANGELOG.md)
- [Documentation](docs/index.md)

## Author and references

Developed and maintained by **Artem Alimpiev**.

- [ORCID: 0009-0007-6740-7242](https://orcid.org/0009-0007-6740-7242)
- Research references: [10.5281/zenodo.20057443](https://doi.org/10.5281/zenodo.20057443),
  [10.5281/zenodo.20659647](https://doi.org/10.5281/zenodo.20659647)

## License

MIT, as declared in the [package metadata](pyproject.toml).
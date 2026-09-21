# Run the dogfooding demo

From a source checkout, run:

```bash
uv sync --locked --all-extras --dev
uv run --locked python -m examples.dogfooding
```

The command runs Django's system checks, applies the included migrations to a
private SQLite database in memory, and executes the scenarios below. File storage
is also in memory. Each invocation starts from an empty database and selects the
demo settings, even if your shell already defines `DJANGO_SETTINGS_MODULE`.
It does not connect to your application's database or require Docker.

Success ends with `Dogfooding demo passed.` A failed check exits with an error.
The example is included in the source repository; it is not installed by the
`django-nova` wheel.

## Models

This is the actual model module executed by the command:

```python
--8<-- "examples/dogfooding/models.py"
```

`Project` uses an automatically generated schema. `Article` uses an explicit
Pydantic schema. `RelaxedArticle` is a Django proxy model that shares the article
table while disabling the Pydantic stage on its save path.

## Scenarios

| Check | Expected result |
|---|---|
| Public imports | `get_default_cache`, `NovaManager`, `TypedField`, and `TypedQuerySet` resolve to the canonical API. |
| Unsaved project | Scalar serialization succeeds before an ID exists; M2M and `internal_note` are omitted. |
| Generated timestamp | `created_at` is not required before save and is populated on save. |
| File | A 4096-byte `ContentFile` is accepted; serialization returns its name, not its content. |
| Decimal input | The string `"12.50"` becomes `Decimal("12.50")` before `Project.clean()` and survives a DB round trip. |
| Business validation | A negative budget raises `NovaValidationError` and creates no row. |
| Strictness | A short title fails for `Article`; `RelaxedArticle` accepts it but still rejects a blank Django field. |
| Task | Awaited engine startup and shutdown surround submission; the decorated call returns an ID before completion. |

After saving the project, the example adds and reads an M2M label through Django.
That does not add nested M2M serialization to Nova's scalar schema contract.

## Adapting the model

In your application, keep Django fields, the Pydantic schema, and database
constraints aligned. Use `Decimal` for monetary comparisons. With
`NovaModel.save()`, Django field conversion happens before `Model.clean()`;
calling `instance.clean()` directly does not execute the preceding stages.
See [validation and serialization](validation.md).

Use normal Django project settings and migrations when adopting the model.
The demo's in-memory settings are only for its standalone process.

## Regression checks

```bash
uv run --locked pytest -q tests/docs/test_dogfooding_demo.py
```

The regression invokes the documented command in a subprocess. It also checks
that the example's migration matches its models. PostgreSQL, remote caching,
and concurrent behavior have separate [integration checks](testing.md).

# Django Nova

Django Nova connects Django models with Pydantic schemas, typed query helpers,
caching, background tasks, and optional tracing. Django remains responsible for
persistence and database semantics.

These pages describe the development checkout. Check the
[changelog](https://github.com/Artem7898/django-nova/blob/main/CHANGELOG.md) for the
version installed in your application. The package is currently marked Beta;
coverage percentages do not establish API stability.

## Start here

1. [Install Nova and configure Django](installation.md).
2. [Run the standalone dogfooding demo](quickstart.md).
3. [Understand validation and serialization](validation.md).
4. [Run the relevant verification suite](testing.md).

## Guides

| Area | Guide |
|---|---|
| Responsibilities and lifecycle | [Architecture](architecture.md) |
| QuerySet caching and invalidation | [Caching](caching.md) |
| Async task submission and lifecycle | [Background tasks](tasks.md) |
| Spans and optional telemetry | [Tracing](tracing.md) |
| Schema changes and PostgreSQL limits | [Migrations](migrations.md) |
| Coverage and remaining work | [Project status](status.md) |

## API reference

[Core](api/core.md) · [Validation](api/validation.md) · [Cache](api/cache.md) ·
[Query planning](api/query.md) · [Ecosystem adapters](api/ecosystem.md) ·
[Database](api/db.md) · [Redis](api/redis.md)

Validation is shared across explicit boundaries: Pydantic schemas, Django fields,
`Model.clean()`, and database constraints each retain their responsibilities.
Static typing complements runtime checks. Async helpers do not make every ORM
operation non-blocking, and caching should be measured against the actual workload.

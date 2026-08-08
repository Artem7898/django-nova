# Django Nova Documentation

Welcome to the documentation of Django Nova— a typed, unified, asynchronous-oriented Django toolkit.
The library is designed with a focus on scientific computing, Highload, and Reproducible Research.

## What is Django Nova?

Django Nova is a modern toolkit that solves Django's key architectural problems.:

- **Duplicate validation** — you no longer need to write validation in forms, serializers, and models separately
- **No strict typing** — full support for `pyright --strict`
- **Caching Issues** — Smart disability without manual control
- **Difficulties with migrations** — built-in support for PostgreSQL concurrent migrations

## Project philosophy

1. **A single source of truth** — all business logic of validation is concentrated in Pydantic schemes
2. **Fail fast** — errors should be detected at the static analysis stage, not in runtime
3. **Default asynchrony** — all operations are designed with `asyncio` in mind
4. **Zero-downtime** — migrations and updates should not interrupt the system operation


## Modules

### `nova.typing`
A strict typing layer. Includes `NovaModel' and `NovaConfig'.
Uses PEP 695 to ensure full type derivability in the IDE (PyCharm, VSCode + Pyright).

### `nova.validation`
Django Unified Bridge <-> Pydantic (`pydantic_bridge`).
Ensures that validation rules are not duplicated between forms, serializers, and models.

### `nova.cache`
Intelligent QuerySet caching ('queryset_cache').
Features:
- Using SQL Compiler to generate hashes (safe with Django updates).
- Reversible index `O(1)` for instant cache invalidation during `save()` or `delete()'.

### `nova.tasks`
Built-in asynchronous task engine based on asyncio.Queue`.
An alternative to Celery for in-process computing (ML inference, simulation).

### `nova.db`
Utilities for secure migrations:
- `zero_downtime.py `: Wrappers over `CREATE INDEX CONCURRENTLY` and `ALTER TABLE' without locks (PostgreSQL).
- `splitter.py `: Breaking down heavy Data Migrations into batches to prevent OOM.

## Documentation content

- [Installation and Configuration](installation.md )
- [Quick Start](quickstart.md )
- [Architectural solutions](architecture.md )
- [API Reference](api.md )
- [Migration Guide](migrations.md )
- [Cache Management](caching.md )
- [Best Practices](best-practices.md )

## Author

Developed and maintained by **Artem Alimpiev**.
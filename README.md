<div align="center">

<img src="assets/django-nova-logo.png" width="250" alt="Django Nova Logo">


# Django Nova

A typed, unified, async-first toolkit for Django 5+.

 [Django Nova site](https://artem7898.github.io/django-nova-site/)

---

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
---
### 📚 More Information
- Full Documentation: docs/index.md
- Auto-generated Status Report: STATUS.md
- Changelog & Release Notes: CHANGELOG.md 

---
### 🚀 Quick Start (5 minutes)
1. Create a typed model
```bash
## src/app/models.pyfrom django.db import modelsfrom nova.typing import NovaModel, NovaConfigfrom pydantic import BaseModelclass ArticleSchema(BaseModel):    title: str    content: str    views: int = 0class Article(NovaModel):    _nova_config = NovaConfig(        pydantic_schema=ArticleSchema,        strict_validation=True,        cache_enabled=True,    )    title: models.CharField(max_length=200)    content: models.TextField()
```

2. Use it with automatic validation


```bash
# Views or services
article = Article(title="Hello Nova", content="Typed Django!")
article.save()  # ✅ Validates against Pydantic schema automatically
```
3.  Enjoy type safety 🎉

```bash
# Pyright --strict compatible! ✅
article.title = 123  # Type error! Expected str
```

## 📦 Installation

Requires **Python 3.12+** and **Django 5.0+** (tested with Django 5.0, 5.1, 5.2).

Using [`uv`](https://docs.astral.sh/uv/) (recommended):

```bash
# Core library
uv add django-nova

# With Django REST Framework support
uv add django-nova[drf]

# With Redis infrastructure & Distributed Locks
uv add django-nova[redis]
# or
uv add django-nova[cache]

# With OpenTelemetry tracing
uv add django-nova[tracing]

# Full enterprise stack (tracing + observability)
uv add django-nova[tracing,observability]

# With FastAPI integration
uv add django-nova[fastapi]

# With async task queue
uv add django-nova[tasks]

# With async database support
uv add django-nova[async]
```

### Add to `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    # ...
    "nova",
]
```

---

## 🎯 Philosophy

> ⚠️ **This is a Beta project.** See the [auto-generated status report](STATUS.md) for real module-by-module coverage and stability assessment.


## 📊 Honest Project Status


## 🚀 Quick Start


## 📊 Current Status

## 🛡️ Validation Boundary

See [STATUS.md](./STATUS.md) for the honest, auto-generated breakdown.

## 📚 API Reference


---



## 👤 Author

**Artem Alimpiev**

- ORCID: [0009-0007-6740-7242](https://orcid.org/0009-0007-6740-7242)
- DOI: [10.5281/zenodo.20057443](https://doi.org/10.5281/zenodo.20057443)
- DOI: [10.5281/zenodo.20659647](https://doi.org/10.5281/zenodo.20659647)
- PyPI: [Django Nova](https://pypi.org/project/django-nova/)
- NOVA: [Django Nova site](https://artem7898.github.io/django-nova-site/)


---
## 📄 License

MIT License. See [LICENSE](LICENSE) for details. 2026









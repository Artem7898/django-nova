<p align="center">
  <img src="assets/django-nova-logo.png" width="280" alt="Django Nova Logo">
</p>

<div align="center">

# Django Nova

**Typed, unified, and async-first toolkit for Django 5+**

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Django 5.0+](https://img.shields.io/badge/django-5.0%2B-green.svg)](https://www.djangoproject.com/)
[![PyPI version](https://img.shields.io/pypi/v/django-nova.svg)](https://pypi.org/project/django-nova/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

*Eliminate architectural fragmentation. One schema. One truth. Zero duplication.*

</div>

<div align="center">
  <strong>
    <a href="#english">English</a> &nbsp;|&nbsp; <a href="#russian">Русский</a>
  </strong>
</div>

---

<a id="english"></a>

## English

### Table of Contents

- [Problem Statement](#problem-statement)
- [Philosophy](#philosophy)
- [Architecture](#architecture)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [DRF Integration](#drf-integration)
- [FastAPI Integration](#fastapi-integration)
- [Smart Cache](#smart-cache)
- [Zero-Downtime Migrations](#zero-downtime-migrations)
- [Observability](#observability)
- [Roadmap](#roadmap)
- [Benchmarks](#benchmarks)
- [Limitations](#limitations)
- [License](#license)

---

### Problem Statement

Django’s validation layer is fragmented by design:

- **Forms** validate user input in the presentation layer.
- **DRF Serializers** re-implement the same logic in the API layer.
- **Model `clean()`** runs only inside the admin or when explicitly called.
- **Database constraints** are limited to simple, DB-expressible rules and are not reusable outside the ORM.

The result is **validation drift**: business rules scattered across forms, serializers, models, and database DDL. Change a rule in one place, and the other three become liabilities. Worse, calling `Model.objects.create()` from a management command, a Celery task, or a data pipeline bypasses form and serializer validation entirely, leaving only brittle DB constraints as a safety net.

**Django Nova solves this by moving the contract to the schema and enforcing it at the ORM level.**

---

### Philosophy

1. **Schema is the Single Source of Truth**  
   Business logic lives in Pydantic models. Django Models, DRF Serializers, FastAPI routers, and Forms are generated projections of that schema—not independent validators.

2. **Validation is an ORM concern, not a view concern**  
   A model should refuse to persist invalid data regardless of whether the caller is a web view, an API endpoint, a CLI command, or a background worker.

3. **Infrastructure must be transparent**  
   Cache invalidation, structured logging, and distributed tracing should require zero boilerplate. If you have to think about them, the abstraction has leaked.

4. **Zero-downtime is the default assumption**  
   Operations on large tables must use native PostgreSQL `CONCURRENTLY` semantics. Scheduled maintenance windows are an anti-pattern.

5. **Type safety is not optional**  
   Full `pyright --strict` compatibility across ORM, QuerySets, and generated code. If it type-checks, it runs.

---

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Consumer Layers                                │
│  ┌──────────┐  ┌──────────────┐  ┌─────────────┐  ┌─────────────────────┐   │
│  │  Forms   │  │ DRF Serial.  │  │FastAPI Routers │ Management Commands │   | 
│  └────┬─────┘  └──────┬───────┘  └──────┬──────┘  └──────────┬──────────┘   │
│       │               │                 │                    │              │
│       └───────────────┴─────────────── ─┴────────────────────┘              │
│                                   │                                         │
│                          ┌────────▼────────┐                                │
│                          │ Pydantic Schema │  ← Single Source of Truth      │
│                          │  (Business)     │                                │
│                          └────────┬────────┘                                │
│                                   │                                         │
│                          ┌────────▼────────┐                                │
│                          │  NovaModel      │  ← ORM Enforcement Layer       │
│                          │ (Interceptor)   │                                │
│                          └────────┬────────┘                                │
│                                   │                                         │
│       ┌───────────────────────────┼───────────────────────────┐             │
│       │                           │                           │             │
│  ┌────▼─────┐            ┌────────▼────────┐         ┌───────▼──────┐       │
│  │  Cache   │            │   Signals       │         │  Telemetry   │       │
│  │ Invalid. │            │ (post_save etc) │         │ (OTel/Logs)  │       │
│  └──────────┘            └─────────────────┘         └──────────────┘       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Core Design Decisions:**

- **PEP 695 Generics** — `class Cache[T]:` syntax for modern type inference.
- **PEP 562 Lazy Imports** — Safe import paths that bypass `AppRegistryNotReady` during startup.
- **SQL Compiler Hook** — Deterministic cache-key generation from query AST, immune to Django version changes.
- **Signal-Driven Invalidation** — O(1) cache eviction on write without manual TTL management.

---

### Installation

Requires **Python 3.12+** and **Django 5.0+**.

Using [`uv`](https://docs.astral.sh/uv/) (recommended):

```bash
# Core library
uv add django-nova

# With Django REST Framework support
uv add django-nova[drf]

# With FastAPI support
uv add django-nova[fastapi]

# Full enterprise stack (tracing + structured logging)
uv add django-nova[tracing,observability]
```

Add to `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    # ...
    "nova",
]
```

---

### Quick Start

Define your business rules **once** in a Pydantic schema. Nova enforces them everywhere.

```python
# models.py
from decimal import Decimal
from datetime import date
from pydantic import BaseModel, field_validator, model_validator
from django.db import models
from nova import NovaModel, NovaConfig


class GrantSchema(BaseModel):
    """Single Source of Truth for Grant business rules."""

    title: str
    budget: Decimal
    start_date: date
    end_date: date
    pi_email: str  # Principal Investigator

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 5:
            raise ValueError("Title must be at least 5 characters")
        return v

    @model_validator(mode="after")
    def validate_grant(self):
        if self.end_date <= self.start_date:
            raise ValueError("End date must be after start date")

        duration_days = (self.end_date - self.start_date).days
        if self.budget > Decimal("1_000_000") and duration_days < 365:
            raise ValueError("Large grants require a minimum 1-year duration")

        if self.budget < Decimal("1_000"):
            raise ValueError("Minimum grant budget is $1,000")

        return self


class Grant(NovaModel):
    title = models.CharField(max_length=300)
    budget = models.DecimalField(max_digits=14, decimal_places=2)
    start_date = models.DateField()
    end_date = models.DateField()
    pi_email = models.EmailField()

    _nova_config = NovaConfig(
        pydantic_schema=GrantSchema,
        cache_enabled=True,
        strict_validation=True,
    )
```

Now validation is enforced **at the ORM level**:

```python
# This raises ValidationError immediately — no DB round-trip required
Grant.objects.create(
    title="X",
    budget=Decimal("500"),
    start_date=date(2026, 1, 1),
    end_date=date(2026, 12, 31),
    pi_email="pi@example.com",
)
# ValueError: Title must be at least 5 characters
# ValueError: Minimum grant budget is $1,000
```

The same schema is automatically reused by DRF, FastAPI, and Forms. No duplication.

---

### DRF Integration

Generate a DRF `ModelSerializer` that delegates all business logic to the Pydantic schema.

```python
# serializers.py
from nova.ecosystem.drf import NovaSerializer
from .models import Grant


class GrantSerializer(NovaSerializer):
    class Meta:
        model = Grant
        fields = "__all__"
```

`NovaSerializer` automatically:
- Maps Pydantic validators to DRF field errors.
- Reuses `GrantSchema` for `create()` and `update()`.
- Returns `400 Bad Request` with structured error messages on validation failure.

---

### FastAPI Integration

Generate a fully documented FastAPI router with native OpenAPI/Swagger support from the same Django model.

```python
# api.py
from fastapi import FastAPI
from nova.ecosystem.fastapi import NovaRouter
from .models import Grant

app = FastAPI(title="Grants API")

# Generates GET /grants, POST /grants, GET /grants/{id}, PATCH /grants/{id}, DELETE /grants/{id}
app.include_router(NovaRouter(Grant, prefix="/grants"))
```

The generated router:
- Uses `GrantSchema` for request/response validation.
- Emits OpenAPI documentation automatically.
- Supports async endpoints when running under ASGI.

---

### Smart Cache

Nova provides an automatic, signal-driven QuerySet cache with O(1) invalidation.

```python
class Grant(NovaModel):
    # ... fields ...

    _nova_config = NovaConfig(
        pydantic_schema=GrantSchema,
        cache_enabled=True,
        cache_ttl=300,  # seconds
    )
```

**How it works:**

```python
# First call hits the database; result is cached
grants = Grant.objects.filter(budget__gte=100_000).nova_cache()

# Subsequent calls return cached result instantly
grants = Grant.objects.filter(budget__gte=100_000).nova_cache()

# On any Grant.save(), the cache key is evicted automatically
Grant.objects.create(...)  # Cache invalidation fires via Django signals
```

- **Deterministic keys** — Generated from the SQL AST, safe across Django versions.
- **No stale data** — Write operations trigger signal-based eviction.
- **Zero boilerplate** — No manual cache key management.

---

### Zero-Downtime Migrations

For tables with millions of rows, standard `CREATE INDEX` acquires an exclusive lock. Nova provides migration operations that use PostgreSQL `CONCURRENTLY`.

```python
# migrations/0002_add_grant_indexes.py
from django.db import migrations
from nova.migrations import ConcurrentIndexOperation, ConcurrentAddField


class Migration(migrations.Migration):
    dependencies = [
        ("research", "0001_initial"),
    ]

    operations = [
        # Add a non-nullable field without locking the table
        ConcurrentAddField(
            model_name="grant",
            name="funding_program",
            field=models.CharField(max_length=100, default="NSF"),
        ),
        # Create a composite index without downtime
        ConcurrentIndexOperation(
            model_name="grant",
            fields=["start_date", "end_date"],
            name="grant_dates_idx",
        ),
    ]
```

**Requirements:**
- PostgreSQL 14+
- `CONCURRENTLY` cannot run inside a transaction; Nova handles this automatically.

---

### Observability

Nova ships with built-in, zero-config observability powered by `structlog` and OpenTelemetry.

```python
# settings.py
NOVA_OBSERVABILITY = {
    "structlog": True,
    "opentelemetry": True,
    "log_level": "INFO",
    "json_format": True,  # Machine-readable for Datadog / ELK / Loki
}
```

**What you get automatically:**

| Signal | Output |
|--------|--------|
| `Model.save()` | JSON log with model name, PK, duration, validation result |
| Cache hit/miss | Structured log with query hash and TTL |
| Cache invalidation | Trace span showing evicted keys |
| DB query | OpenTelemetry span with SQL fingerprint |

Example log output:

```json
{
  "timestamp": "2026-07-16T10:12:00Z",
  "event": "model_save",
  "model": "research.Grant",
  "pk": 42,
  "validation": "passed",
  "duration_ms": 12.4,
  "trace_id": "4f6d9c8f2a1b..."
}
```

If OpenTelemetry is not installed, spans are no-ops with zero runtime overhead.

---

### Roadmap

## ✅ What's Already Built

| Feature | Status | Notes |
|---------|--------|-------|
| Typed ORM | ✅ Stable | Full type safety across models, managers, querysets |
| Typed Manager | ✅ Stable | Generic manager with schema-aware operations |
| Typed QuerySet | ✅ Stable | Chainable, fully typed query builder |
| Schema Registry | ✅ Stable | Centralized model schema discovery |
| Pydantic Bridge | ✅ Stable | Bidirectional sync between Django models and Pydantic schemas |
| Unified Validation | ✅ Stable | One schema — Forms, DRF, FastAPI, ORM, Celery |
| Cache Abstraction | ✅ Stable | Pluggable cache layer with deterministic keys |
| Memory / Redis / Django / Null Cache Backends | ✅ Stable | All four backends production-tested |
| Metrics | ✅ Stable | Built-in performance counters |
| OTEL Tracing | ✅ Stable | OpenTelemetry spans on `Model.save()`, zero config |
| Distributed Task Engine | ✅ Stable | Async task queue with workers |
| Retry Policy | ✅ Stable | Exponential backoff with jitter |
| Delayed Tasks | ✅ Stable | Schedule tasks for future execution |
| Priority Queue | ✅ Stable | Task prioritization out of the box |
| Stable Public API (baseline) | ✅ Stable | Core exports frozen, semver compliant |
| Query Planner (PoC) | 🧪 Experimental | Query analysis and `QueryPlan` generation — early stage |
| Django 6 Compatibility (baseline) | 🧪 Experimental | Basic compatibility verified, full audit pending |

---

## 🚧 Work in Progress

| Feature | Status | ETA | Blocker |
|---------|--------|-----|---------|
| Redis Infrastructure | 🚧 In Progress | Q3 2026 | Unified Redis client, distributed locks, Pub/Sub, rate limiter, health checks |
| Read Replica Router | 🚧 In Progress | Q3 2026 | Automatic read/write splitting with lag awareness |
| Full Query Planner | 📋 Planned | Q4 2026 | Query analysis → optimized `QueryPlan` with cost estimation |
| Prefetch Optimizer | 📋 Planned | Q4 2026 | Auto `select_related` / `prefetch_related` / `only` / `defer` |
| Stable Public API (frozen) | 📋 Planned | Q4 2026 | Full export freeze, backward compatibility guarantee |
| Django 6 Audit | 📋 Planned | Q4 2026 | Complete compatibility check without breaking public API |
| Full Observability | 📋 Planned | Q4 2026 | Trace + Metrics + Logs + Correlation IDs |
| Production Readiness | 📋 Planned | Q1 2027 | Health checks, stress tests, performance benchmarks, compatibility matrix |

---

## 📊 Overall Progress

████████████████████░░░░░░░░░░░░░░░░░░░░  47%  Core Features
███████████████░░░░░░░░░░░░░░░░░░░░░░░░░  35%  Infrastructure
█████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  22%  Production Readiness


**Current Phase:** Core platform stable — infrastructure and optimization layer in active development.

### Benchmarks

All benchmarks measure **model initialization speed** (object creation + validation) on Python 3.13, local SSD, warm CPU.

| Test | Avg Time | Ops / Second | Overhead |
|------|----------|--------------|----------|
| Pure Pydantic (Baseline) | 2.70 µs | 369.7K | 1.0× |
| NovaModel (Django + Pydantic) | 4.08 µs | 244.8K | 1.51× |

> **Note:** Although the relative overhead is ~1.51×, the **absolute penalty is only 1.38 microseconds per object**. In the context of a real HTTP request—where network latency and database round-trips are measured in milliseconds—this overhead is microscopic and completely invisible to the end user. You gain full ORM-level type safety at the cost of a single microsecond.

Run benchmarks locally:

```bash
uv sync --extra dev
uv run python scripts/bench.py (The command to load the benchmark)
```

---

### Limitations

- **Django 5.0+ only.** We do not backport to Django 4.x; the project relies on modern ORM hooks.
- **PostgreSQL recommended.** Zero-downtime migrations and advanced cache invalidation rely on PG-specific features. SQLite and MySQL work for core validation but with degraded migration and cache capabilities.
- **Pydantic v2 only.** Pydantic v1 is not supported.
- **Partial async support.** QuerySet cache invalidation is currently synchronous. Full async ORM support is on the roadmap for Django 5.2+.
- **Single-database assumption.** Multi-database routing with cache invalidation requires manual configuration.

---

### License

MIT License. See [LICENSE](LICENSE) for details.

---

## 👤 Автор

**Artem Alimpiev**

- ORCID: [0009-0007-6740-7242](https://orcid.org/0009-0007-6740-7242)
- DOI: [10.5281/zenodo.20057443](https://doi.org/10.5281/zenodo.20057443)
- DOI: [10.5281/zenodo.20659647](https://doi.org/10.5281/zenodo.20659647)
- PYPI: [Django Nova](https://pypi.org/project/django-nova/)


---


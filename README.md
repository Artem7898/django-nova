<div align="center">

# 🚀 Django Nova

**Typed, unified, and async-first toolkit for Django 5+**

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Django 5.0+](https://img.shields.io/badge/django-5.0%2B-green.svg)](https://www.djangoproject.com/)
[![PyPI version](https://img.shields.io/pypi/v/django-nova.svg)](https://pypi.org/project/django-nova/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

*Django Nova eliminates fundamental architectural flaws in Django that lead to data corruption, runtime errors, and maintainability issues in scientific and enterprise software.*

</div>

---

## 🔑 Key Innovations

- ✅ **Single Source of Truth:** Define validation once in Pydantic. Django Models, Forms, and APIs automatically read from it. No more duplication.
- 🔒 **Strict Type Safety:** Full `pyright --strict` compatibility for ORM, QuerySets, and Models using modern PEP 695 syntax.
- ⚡ **Smart QuerySet Cache:** Automatic O(1) cache invalidation on write via Django signals. Zero percent stale data in research pipelines.
- 🔄 **Zero-Downtime Migrations:** Native PostgreSQL `CONCURRENTLY` operations for locked tables containing millions of rows.
- 📊 **Structured Observability:** Built-in `structlog` integration emitting machine-readable JSON logs with ISO-timestamps for Datadog/ELK.
- 🔍 **Distributed Tracing:** OpenTelemetry spans for `Model.save()` and Cache operations. Zero overhead if OTEL is not installed.
- 🔌 **DRF Auto-Serializer:** Generate Django Rest Framework Serializers dynamically from Pydantic schemas. Pydantic validation overrides DRF.
- 🚀 **FastAPI Auto-Router:** Generate fully documented FastAPI routers with native OpenAPI/Swagger from Django models.

---

## 🚀 Quick Start

### Installation

```bash
# Core library
pip install django-nova

# With DRF support
pip install django-nova[drf]

# With FastAPI support
pip install django-nova[fastapi]

# With full enterprise stack (tracing, logging)
pip install django-nova[tracing,observability]
```

---

## 💡 Usage Example

### Define your rules once, use them everywhere:

```python
# models.py
from pydantic import BaseModel, field_validator
from django.db import models
from nova import NovaModel, NovaConfig

# 1. Define validation rules (ONCE)
class ResearcherSchema(BaseModel):
    name: str
    h_index: int = 0

    @field_validator("h_index")
    @classmethod
    def validate_h_index(cls, v: int) -> int:
        if v < 0:
            raise ValueError("h-index cannot be negative")
        return v

# 2. Link to Django
class Researcher(NovaModel):
    name = models.CharField(max_length=300)
    h_index = models.IntegerField(default=0)

    _nova_config = NovaConfig(
        pydantic_schema=ResearcherSchema,
        cache_enabled=True,
        strict_validation=True
    )
```

**Now, any attempt to save invalid data is blocked at the ORM level, and the schema is automatically reused in DRF and FastAPI!**

---

## 🔗 Ecosystem Integration

Django Nova acts as a universal hub between Python frameworks.

### Django Rest Framework

```python
from nova.ecosystem.drf import to_drf_serializer

# Dynamically generates a ModelSerializer that delegates business logic to Pydantic
ResearcherSerializer = to_drf_serializer(Researcher)
```

### FastAPI

```python
from fastapi import FastAPI
from nova.ecosystem.fastapi import to_fastapi_router

app = FastAPI()
# Generates GET/POST endpoints with native OpenAPI/Swagger documentation
app.include_router(to_fastapi_router(Researcher, prefix="/api/researchers"))
```

---

## 🏗️ Architecture

Django Nova intercepts standard processes at the core level:

```text
Request -> View -> Model.save() -> [Pydantic Validation -> Django Fields -> Business Logic] -> DB
                |
                +-> Cache Invalidation Signal -> Evict stale QuerySets
                |
                +-> OpenTelemetry Span -> Metrics & Traces
                |
                +-> Structlog -> JSON Logs to Datadog/ELK
```

### Core Tech Stack:

- **PEP 562:** Lazy imports bypassing AppRegistryNotReady.
- **PEP 695:** Modern generic syntax (`class Cache[T]:`).
- **SQL Compiler:** Deterministic cache hash key generation (safe across any Django version).

---

## 🧪 Testing

The project is tested on the bleeding-edge stack (Python 3.14 + Django 5.2).

```bash
pip install -e ".[dev]"
pytest tests/ -v  # 39 passed
```

---

## 👤 Author

**Artem Alimpiev**

- ORCID: [0009-0007-6740-7242](https://orcid.org/0009-0007-6740-7242)
- DOI: [10.5281/zenodo.20057443](https://doi.org/10.5281/zenodo.20057443)
- DOI: [10.5281/zenodo.20659647](https://doi.org/10.5281/zenodo.20659647)

---

## 📄 License

This project is licensed under the terms of the MIT License. See the [LICENSE](LICENSE) file for details.
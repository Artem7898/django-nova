<div align="center">

# 🌠 Django Nova

**Typed, unified, and async-first toolkit for Django 5+**

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Django 5.0+](https://img.shields.io/badge/django-5.0%2B-green.svg)](https://www.djangoproject.com/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

*Django Nova eliminates fundamental architectural flaws in Django that lead to data corruption, runtime errors, and maintainability issues in scientific and enterprise software.*

</div>

---

## 🚀 Key Innovations

- 🔒 **Single Source of Truth for Validation:** Define your validation once in Pydantic. Django Models, Forms, and APIs will automatically read from it. No more logic duplication.
- 🏗️ **Strict Type Safety:** Full `pyright --strict` compatibility for ORM, QuerySets, and Models. Perfect IDE autocompletion.
- ⚡ **Intelligent QuerySet Caching:** Automatic cache invalidation on write (via Django signals). Zero percent stale data in research pipelines.
- 🛠️ **Zero-Downtime Migrations:** Native PostgreSQL `CONCURRENTLY` operations for locked tables containing millions of rows.
- ⚙️ **Built-in Task Engine:** `asyncio`-based task runner. Execute background computations without the need to set up Celery/RabbitMQ for simple batch jobs.

---

## 📖 Quick Start

### Installation

```bash
pip install django-nova
Usage example
# models.py
from pydantic import BaseModel, field_validator
from django.db import models
from nova import NovaModel, NovaConfig

# 1. Define validation rules (ONCE)
class ResearcherSchema(BaseModel):
    name: str
    email: str
    h_index: int = 0

    @field_validator("h_index")
    @classmethod
    def validate_h_index(cls, v: int) -> int:
        if v < 0:
            raise ValueError("h-index cannot be negative")
        return v

# 2. Connect to Django
class Researcher(NovaModel):
    name = models.CharField(max_length=300)
    email = models.EmailField(unique=True)
    h_index = models.IntegerField(default=0)

    _nova_config = NovaConfig(
        pydantic_schema=ResearcherSchema,
        cache_enabled=True,
        strict_validation=True
    )

Now, any attempt to save invalid data will be blocked at the ORM level:
# Throws NovaValidationError before it even hits the database!
bad_researcher = Researcher(name="John Doe", email="john@test.com", h_index=-5)
bad_researcher.save() 

🏛️ Architecture
Django Nova integrates seamlessly, intercepting standard Django processes at the core level:
Request -> View -> Model.save() -> [Pydantic Validation -> Django Fields -> Business Logic] -> DB
                |
                +-> Cache Invalidation Signal -> Evict stale QuerySets
Core Tech Stack:

PEP 562: Lazy imports to bypass AppRegistryNotReady.
PEP 695: Modern generic syntax (class Cache[T]:).
SQL Compiler: Deterministic cache hash key generation (safe across any Django version).

🧪 Testing
The project is tested on the bleeding-edge stack (Python 3.14 + Django 5.2). Clone the repository and run:

pip install -e ".[dev]"
pytest tests/test_full_integration.py -v

📚 Documentation
Full documentation on architecture, API, and migration utilities is available in the docs/ directory.

👤 Author
Artem Alimpiev

📄 License
This project is licensed under the terms of the MIT license. See the LICENSE file for details.
https://test.pypi.org/project/django-nova/0.1.0/

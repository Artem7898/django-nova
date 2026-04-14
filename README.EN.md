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

# How everyone does it (Classic Django + DRF):

# 1. models.py
class Article(models.Model):
    title = models.CharField(max_length=200)
    status = models.CharField(max_length=20)

    def clean(self):
        if self.status not in ("DRAFT", "PUBLISHED"):
            raise ValidationError("Invalid status")

# 2. serializers.py (DUPLICATION!)
class ArticleSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    status = serializers.ChoiceField(choices=["DRAFT", "PUBLISHED"])

# 3. forms.py (MORE DUPLICATION!)
class ArticleForm(forms.ModelForm):
    class Meta:
        model = Article
        fields = "__all__"

    def clean_status(self):
        # And so it is in all projects 100 times...
        pass

How it's done with Django Nova:

# 1. schema.py (THE ONLY SOURCE OF TRUTH)
from pydantic import BaseModel

class ArticleSchema(BaseModel):
    title: str
    status: Literal["DRAFT", "PUBLISHED"]

# 2. models.py (THAT'S IT, YOU DON'T NEED ANYTHING ELSE)
class Article(NovaModel):
    title = models.CharField(max_length=200)
    status = models.CharField(max_length=20)

    _nova_config = NovaConfig(
        pydantic_schema=ArticleSchema,
        cache_enabled=True, 
        strict_validation=True
    )

# Any call to article.save() is automatically run through ArticleSchema.
# Forms and API are generated from the schema automatically.

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

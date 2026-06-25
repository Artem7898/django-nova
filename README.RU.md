<div align="center">

# 🚀 Django Nova

**Типизированный, унифицированный и async-first инструментарий для Django 5+**

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Django 5.0+](https://img.shields.io/badge/django-5.0%2B-green.svg)](https://www.djangoproject.com/)
[![PyPI version](https://img.shields.io/pypi/v/django-nova.svg)](https://pypi.org/project/django-nova/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

*Django Nova устраняет фундаментальные архитектурные недостатки Django, приводящие к порче данных, ошибкам времени выполнения и проблемам сопровождаемости в научном и корпоративном ПО.*

</div>

---

## 🔑 Ключевые инновации

- ✅ **Единый источник правды:** Определяйте валидацию один раз в Pydantic. Модели Django, формы и API автоматически используют её. Никакого дублирования.
- 🔒 **Строгая типобезопасность:** Полная совместимость с `pyright --strict` для ORM, QuerySet и моделей с использованием современного синтаксиса PEP 695.
- ⚡ **Умный кэш QuerySet:** Автоматическая O(1) инвалидация кэша при записи через сигналы Django. Ноль процентов устаревших данных в исследовательских конвейерах.
- 🔄 **Миграции без простоя:** Нативные операции PostgreSQL `CONCURRENTLY` для заблокированных таблиц с миллионами строк.
- 📊 **Структурированная наблюдаемость:** Встроенная интеграция `structlog` с генерацией машиночитаемых JSON-логов с ISO-метками времени для Datadog/ELK.
- 🔍 **Распределённая трассировка:** OpenTelemetry-спаны для `Model.save()` и операций кэша. Нулевые накладные расходы, если OTEL не установлен.
- 🔌 **DRF Auto-Serializer:** Динамическая генерация Django Rest Framework Serializers из Pydantic-схем. Валидация Pydantic имеет приоритет над DRF.
- 🚀 **FastAPI Auto-Router:** Генерация полностью документированных FastAPI-роутеров с нативным OpenAPI/Swagger из моделей Django.

---

## 🚀 Быстрый старт

### Установка

```bash
# Базовая библиотека
pip install django-nova

# С поддержкой DRF
pip install django-nova[drf]

# С поддержкой FastAPI
pip install django-nova[fastapi]

# С полным enterprise-стеком (трассировка, логирование)
pip install django-nova[tracing,observability]
```

---

## 💡 Пример использования

### Определите правила один раз — используйте их везде:

```python
# models.py
from pydantic import BaseModel, field_validator
from django.db import models
from nova import NovaModel, NovaConfig

# 1. Определите правила валидации (ОДИН РАЗ)
class ResearcherSchema(BaseModel):
    name: str
    h_index: int = 0

    @field_validator("h_index")
    @classmethod
    def validate_h_index(cls, v: int) -> int:
        if v < 0:
            raise ValueError("h-index не может быть отрицательным")
        return v

# 2. Свяжите с Django
class Researcher(NovaModel):
    name = models.CharField(max_length=300)
    h_index = models.IntegerField(default=0)

    _nova_config = NovaConfig(
        pydantic_schema=ResearcherSchema,
        cache_enabled=True,
        strict_validation=True
    )
```

**Теперь любая попытка сохранить невалидные данные блокируется на уровне ORM, а схема автоматически переиспользуется в DRF и FastAPI!**

---

## 🔗 Интеграция с экосистемой

Django Nova выступает универсальным хабом между Python-фреймворками.

### Django Rest Framework

```python
from nova.ecosystem.drf import to_drf_serializer

# Динамически генерирует ModelSerializer, делегирующий бизнес-логику Pydantic
ResearcherSerializer = to_drf_serializer(Researcher)
```

### FastAPI

```python
from fastapi import FastAPI
from nova.ecosystem.fastapi import to_fastapi_router

app = FastAPI()
# Генерирует GET/POST эндпоинты с нативной документацией OpenAPI/Swagger
app.include_router(to_fastapi_router(Researcher, prefix="/api/researchers"))
```

---

## 🏗️ Архитектура

Django Nova перехватывает стандартные процессы на уровне ядра:

```text
Request -> View -> Model.save() -> [Pydantic Validation -> Django Fields -> Business Logic] -> DB
                |
                +-> Cache Invalidation Signal -> Удаление устаревших QuerySet
                |
                +-> OpenTelemetry Span -> Метрики и трейсы
                |
                +-> Structlog -> JSON-логи в Datadog/ELK
```

### Технологический стек ядра:

- **PEP 562:** Ленивые импорты, обходящие AppRegistryNotReady.
- **PEP 695:** Современный синтаксис дженериков (`class Cache[T]:`).
- **SQL Compiler:** Детерминированная генерация хэш-ключа кэша (безопасна для любой версии Django).

---

## 🧪 Тестирование

Проект тестируется на передовом стеке (Python 3.14 + Django 5.2).

```bash
pip install -e ".[dev]"
pytest tests/ -v  # 42 passed
```

---

## 👤 Автор

**Артем Алимпиев**

- ORCID: [0009-0007-6740-7242](https://orcid.org/0009-0007-6740-7242)
- DOI: [10.5281/zenodo.20057443](https://doi.org/10.5281/zenodo.20057443)
- DOI: [10.5281/zenodo.20659647](https://doi.org/10.5281/zenodo.20659647)
- PYPI: [Django Nova](https://pypi.org/project/django-nova/)

---

## 📄 Лицензия

Проект распространяется на условиях лицензии MIT. Подробности см. в файле [LICENSE](LICENSE).

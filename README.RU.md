<p align="center">
  <img src="assets/django-nova-logo.png" width="280" alt="Django Nova Logo">
</p>

<div align="">

## Django Nova
### Типизированный, унифицированный и асинхронный -первый инструментарий для Django 5

<a id="russian"></a>

## Русский

### Содержание

- [Проблема](#проблема)
- [Философия](#философия)
- [Архитектура](#архитектура)
- [Установка](#установка)
- [Быстрый старт](#быстрый-старт)
- [Интеграция с DRF](#интеграция-с-drf)
- [Интеграция с FastAPI](#интеграция-с-fastapi)
- [Умный кэш](#умный-кэш)
- [Миграции без простоя](#миграции-без-простоя)
- [Наблюдаемость](#наблюдаемость)
- [Дорожная карта](#дорожная-карта)
- [Бенчмарки](#бенчмарки)
- [Ограничения](#ограничения)
- [Лицензия](#лицензия)

---

### Проблема

Валидация в Django фрагментирована по архитектуре:

- **Формы** проверяют пользовательский ввод на уровне представления.
- **DRF-сериализаторы** дублируют ту же логику на уровне API.
- **`clean()` в модели** вызывается только в админке или при явном вызове.
- **Ограничения БД** ограничены простыми выражениями и не переиспользуются за пределами ORM.

Результат — **дрейф валидации**: бизнес-правила разбросаны по формам, сериализаторам, моделям и DDL. Измените правило в одном месте — три других становятся источником багов. Хуже того, вызов `Model.objects.create()` из management-команды, Celery-задачи или data pipeline обходит валидацию форм и сериализаторов полностью, оставляя только хрупкие ограничения БД.

**Django Nova решает это, перемещая контракт в схему и принудительно применяя его на уровне ORM.**

---

### Философия

1. **Схема — единый источник правды**  
   Бизнес-логика живёт в Pydantic-моделях. Django Models, DRF Serializers, FastAPI-роутеры и Forms — это генерируемые проекции схемы, а не независимые валидаторы.

2. **Валидация — это задача ORM, а не представления**  
   Модель должна отказываться сохранять невалидные данные независимо от того, кто вызывает сохранение: web-view, API-эндпоинт, CLI-команда или фоновый воркер.

3. **Инфраструктура должна быть прозрачной**  
   Инвалидация кэша, структурированное логирование и распределённая трассировка не должны требовать шаблонного кода. Если вы вынуждены думать о них — абстракция протекает.

4. **Zero-downtime — это дефолтное предположение**  
   Операции над большими таблицами должны использовать нативную семантику PostgreSQL `CONCURRENTLY`. Плановые окна обслуживания — антипаттерн.

5. **Типобезопасность не опциональна**  
   Полная совместимость с `pyright --strict` на уровне ORM, QuerySet и генерируемого кода. Если проходит type-checking — работает.

---

### Архитектура

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Потребительские слои                           │
│  ┌──────────┐  ┌──────────────┐  ┌─────────────┐  ┌─────────────────────┐   │
│  │  Forms   │  │ DRF Serial.  │  │ FastAPI Routers│  │ Management Commands │   │
│  └────┬─────┘  └──────┬───────┘  └──────┬──────┘  └──────────┬──────────┘   │
│       │               │                │                    │              │
│       └───────────────┴────────────────┴────────────────────┘              │
│                                   │                                         │
│                          ┌────────▼────────┐                                │
│                          │  Pydantic Schema │  ← Единый источник правды    │
│                          │   (Business)     │                                │
│                          └────────┬────────┘                                │
│                                   │                                         │
│                          ┌────────▼────────┐                                │
│                          │   NovaModel       │  ← Слой принудительного     │
│                          │  (Interceptor)    │     применения в ORM         │
│                          └────────┬────────┘                                │
│                                   │                                         │
│       ┌───────────────────────────┼───────────────────────────┐             │
│       │                           │                           │             │
│  ┌────▼─────┐            ┌────────▼────────┐         ┌───────▼──────┐     │
│  │  Cache   │            │   Signals       │         │  Telemetry   │     │
│  │ Invalid. │            │ (post_save etc) │         │ (OTel/Logs)  │     │
│  └──────────┘            └─────────────────┘         └──────────────┘     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Ключевые архитектурные решения:**

- **PEP 695 Generics** — Синтаксис `class Cache[T]:` для современного вывода типов.
- **PEP 562 Lazy Imports** — Безопасные пути импорта, обходящие `AppRegistryNotReady` при старте.
- **SQL Compiler Hook** — Детерминированная генерация ключа кэша из AST запроса, независимая от версии Django.
- **Signal-Driven Invalidation** — Инвалидация кэша за O(1) при записи без ручного управления TTL.

---

### Установка

Требуется **Python 3.12+** и **Django 5.0+**.

Через [`uv`](https://docs.astral.sh/uv/) (рекомендуется):

```bash
# Ядро
uv add django-nova

# С поддержкой Django REST Framework
uv add django-nova[drf]

# С поддержкой FastAPI
uv add django-nova[fastapi]

# Полный enterprise-стек (трассировка + структурированное логирование)
uv add django-nova[tracing,observability]
```

Добавьте в `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    # ...
    "nova",
]
```

---

### Быстрый старт

Определите бизнес-правила **один раз** в Pydantic-схеме. Nova применяет их везде.

```python
# models.py
from decimal import Decimal
from datetime import date
from pydantic import BaseModel, field_validator, model_validator
from django.db import models
from nova import NovaModel, NovaConfig


class GrantSchema(BaseModel):
    """Единый источник правды для бизнес-логики Grant."""

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

Теперь валидация применяется **на уровне ORM**:

```python
# ValidationError выбрасывается немедленно — без round-trip в БД
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

Та же схема автоматически переиспользуется в DRF, FastAPI и Forms. Никакого дублирования.

---

### Интеграция с DRF

Генерация DRF `ModelSerializer`, который делегирует всю бизнес-логику Pydantic-схеме.

```python
# serializers.py
from nova.ecosystem.drf import NovaSerializer
from .models import Grant


class GrantSerializer(NovaSerializer):
    class Meta:
        model = Grant
        fields = "__all__"
```

`NovaSerializer` автоматически:
- Маппит Pydantic-валидаторы на DRF-ошибки полей.
- Переиспользует `GrantSchema` для `create()` и `update()`.
- Возвращает `400 Bad Request` со структурированными сообщениями об ошибках.

---

### Интеграция с FastAPI

Генерация полностью документированного FastAPI-роутера с нативной поддержкой OpenAPI/Swagger из той же Django-модели.

```python
# api.py
from fastapi import FastAPI
from nova.ecosystem.fastapi import NovaRouter
from .models import Grant

app = FastAPI(title="Grants API")

# Генерирует GET /grants, POST /grants, GET /grants/{id}, PATCH /grants/{id}, DELETE /grants/{id}
app.include_router(NovaRouter(Grant, prefix="/grants"))
```

Сгенерированный роутер:
- Использует `GrantSchema` для валидации запросов/ответов.
- Автоматически генерирует OpenAPI-документацию.
- Поддерживает async-эндпоинты при работе через ASGI.

---

### Умный кэш

Nova предоставляет автоматический кэш QuerySet на сигналах с O(1)-инвалидацией.

```python
class Grant(NovaModel):
    # ... fields ...

    _nova_config = NovaConfig(
        pydantic_schema=GrantSchema,
        cache_enabled=True,
        cache_ttl=300,  # секунды
    )
```

**Как это работает:**

```python
# Первый вызов идёт в БД; результат кэшируется
grants = Grant.objects.filter(budget__gte=100_000).nova_cache()

# Последующие вызовы возвращают закэшированный результат мгновенно
grants = Grant.objects.filter(budget__gte=100_000).nova_cache()

# При любом Grant.save() ключ кэша инвалидируется автоматически
Grant.objects.create(...)  # Инвалидация кэша срабатывает через Django-сигналы
```

- **Детерминированные ключи** — Генерируются из AST SQL-запроса, безопасны для любой версии Django.
- **Нет stale-данных** — Операции записи триггерят инвалидацию через сигналы.
- **Ноль бойлерплейта** — Нет ручного управления ключами кэша.

---

### Миграции без простоя

Для таблиц с миллионами строк стандартный `CREATE INDEX` захватывает эксклюзивную блокировку. Nova предоставляет операции миграций с использованием PostgreSQL `CONCURRENTLY`.

```python
# migrations/0002_add_grant_indexes.py
from django.db import migrations
from nova.migrations import ConcurrentIndexOperation, ConcurrentAddField


class Migration(migrations.Migration):
    dependencies = [
        ("research", "0001_initial"),
    ]

    operations = [
        # Добавление non-nullable поля без блокировки таблицы
        ConcurrentAddField(
            model_name="grant",
            name="funding_program",
            field=models.CharField(max_length=100, default="NSF"),
        ),
        # Создание составного индекса без простоя
        ConcurrentIndexOperation(
            model_name="grant",
            fields=["start_date", "end_date"],
            name="grant_dates_idx",
        ),
    ]
```

**Требования:**
- PostgreSQL 14+
- `CONCURRENTLY` не может выполняться внутри транзакции; Nova обрабатывает это автоматически.

---

### Наблюдаемость

Nova поставляется со встроенной observability на базе `structlog` и OpenTelemetry с нулевой конфигурацией.

```python
# settings.py
NOVA_OBSERVABILITY = {
    "structlog": True,
    "opentelemetry": True,
    "log_level": "INFO",
    "json_format": True,  # Machine-readable для Datadog / ELK / Loki
}
```

**Что вы получаете автоматически:**

| Сигнал | Вывод |
|--------|-------|
| `Model.save()` | JSON-лог с именем модели, PK, длительностью, результатом валидации |
| Cache hit/miss | Структурированный лог с хэшем запроса и TTL |
| Инвалидация кэша | Trace span с инвалидированными ключами |
| DB-запрос | OpenTelemetry span с SQL-fingerprint |

Пример лога:

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

Если OpenTelemetry не установлен, span'ы становятся no-op с нулевым оверхедом.

---

### Дорожная карта

#### ✅ Уже реализовано

| Функциональность | Статус |
|------------------|--------|
| Typed Manager | **Готово** |
| Schema Registry | **Готово** |
| Cache Backend Abstraction | **Готово** |
| AppConfig Auto Discovery | **Готово** |
| Typed Settings | **Готово** |

#### 🚧 В разработке и планах

| Функциональность | Статус | Срок |
|------------------|--------|------|
| Redis Cache | Запланировано | Q3 2026 |
| Task Backend | Запланировано | Q3 2026 |
| Metrics | Запланировано | Q4 2026 |
| Instrumentation | Запланировано | Q4 2026 |
| Read Replicas | Запланировано | Q4 2026 |
| Query Planner | Запланировано | Q1 2027 |
| Prefetch Optimizer | Запланировано | Q1 2027 |
| Stable API | Запланировано | Q1 2027 |
| Django 6 Support | Запланировано | Q2 2027 |
| Full OpenTelemetry Support | Запланировано | Q2 2027 |
| Production Readiness | Запланировано | Q2 2027 |

---

### Бенчмарки

Все замеры измеряют **скорость инициализации модели** (создание объекта + валидация) на Python 3.13, локальный SSD, тёплый CPU.

| Тест | Среднее время | Операций в секунду | Накладные расходы |
|------|---------------|--------------------|-------------------|
| Чистый Pydantic (Baseline) | 2.70 µs | 369.7K | 1.0× |
| NovaModel (Django + Pydantic) | 4.08 µs | 244.8K | 1.51× |

> **Примечание:** Хотя относительное замедление составляет ~1.51×, **абсолютное время добавляется всего 1.38 микросекунды на один объект**. В контексте реального HTTP-запроса — где сетевые задержки и работа БД занимают миллисекунды — этот оверхед микроскопический и полностью незаметен для конечного пользователя. Вы получаете безопасность типов на уровне ORM за цену одной микросекунды.

Локальный запуск бенчмарков:

```bash
uv sync --extra dev
uv run python scripts/bench.py  (Команда для запуска бенчмарка)
```

---

### Ограничения

- **Только Django 5.0+.** Мы не бэкпортируем на Django 4.x; проект опирается на современные хуки ORM.
- **Рекомендуется PostgreSQL.** Zero-downtime миграции и продвинутая инвалидация кэша зависят от специфичных для PG возможностей. SQLite и MySQL работают для базовой валидации, но с ограниченными возможностями миграций и кэша.
- **Только Pydantic v2.** Pydantic v1 не поддерживается.
- **Частичная поддержка async.** Инвалидация кэша QuerySet в настоящее время синхронна. Полная поддержка async ORM запланирована для Django 5.2+.
- **Предположение о единой БД.** Мульти-database routing с инвалидацией кэша требует ручной конфигурации.

---

### Лицензия

MIT License. Подробности в файле [LICENSE](LICENSE).
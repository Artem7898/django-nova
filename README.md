<div align="center">

# 🌠 Django Nova

**Типизированный, унифицированный, асинхронно-ориентированный инструментарий для Django 5+**

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Django 5.0+](https://img.shields.io/badge/django-5.0%2B-green.svg)](https://www.djangoproject.com/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

*Django Nova устраняет фундаментальные архитектурные недостатки Django, которые приводят к повреждению данных, ошибкам во время выполнения и проблемам с поддержкой в научном и корпоративном программном обеспечении.*

</div>

---

## 🚀 Основные инновации

- 🔒 **Единый источник достоверной информации (Single Source of Truth):** Определяйте валидацию один раз в Pydantic. Модели Django, формы и API будут автоматически считывать данные из неё. Больше никакого дублирования логики.
- 🏗️ **Строгая типобезопасность:** Полная `pyright --strict` совместимость для ORM, QuerySets и Models. Идеальная работа с IDE.
- ⚡ **Интеллектуальное кэширование QuerySet:** Автоматическая инвалидация кэша при записи (через сигналы Django). Нулевой процент устаревших данных (stale data) в исследовательских конвейерах.
- 🛠️ **Миграции без простоев (Zero-Downtime):** Нативные операции PostgreSQL `CONCURRENTLY` для заблокированных таблиц объемом в миллионы строк.
- ⚙️ **Встроенный движок задач:** `asyncio`-based таск-раннер. Выполняйте фоновые вычисления без необходимости настройки Celery/RabbitMQ для простых пакетных задач.

---

## 📖 Быстрый старт

### Установка

```bash
pip install django-nova

Пример использования.
# models.py

from pydantic import BaseModel, field_validator
from django.db import models
from nova import NovaModel, NovaConfig

# 1. Описываем правила валидации (ОДИН РАЗ)
class ResearcherSchema(BaseModel):
    name: str
    email: str
    h_index: int = 0

    @field_validator("h_index")
    @classmethod
    def validate_h_index(cls, v: int) -> int:
        if v < 0:
            raise ValueError("h-index не может быть отрицательным")
        return v

# 2. Связываем с Django
class Researcher(NovaModel):
    name = models.CharField(max_length=300)
    email = models.EmailField(unique=True)
    h_index = models.IntegerField(default=0)

    _nova_config = NovaConfig(
        pydantic_schema=ResearcherSchema,
        cache_enabled=True,
        strict_validation=True
    )
    
Теперь любая попытка сохранить
# Выбросит NovaValidationError ещё до попадания в БД!
bad_researcher = Researcher(name="Иван", email="ivan@test.com", h_index=-5)
bad_researcher.save() 

🏛️ Архитектура
Джанго Нова незаметно перехватывает стандартные процессы.
Request -> View -> Model.save() -> [Pydantic Validation -> Django Fields -> Business Logic] -> DB
                |
                +-> Cache Invalidation Signal -> Evict stale QuerySets

Технический стек ядра:
PEP 562: Ленивые импортыAppRegistryNotReady.
PEP 695: Современный синтаксис дженclass Cache[T]:).
Компилятор SQL: детерминированная генерация хеш-ключей кэша (безопасно для любой версии Django).

🧪 Тестовая карта
Проект протестирован на новейшей платформе (Python 3.14 + Django 5.2).

pip install -e ".[dev]"
pytest tests/test_full_integration.py -v

📚 Полная документация по архитектуре, API и утилитам миграции доступна в [ ](docs/


👤 Автор
Артем Алимпиев

📄 Лицензия
Данный проект действует на условиях лицензии MIT. Подробности смотрите в файле docs ЛИЦЕНЗИЯ.



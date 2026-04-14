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

# Как делают все (Классический Django + DRF):

# 1. models.py
class Article(models.Model):
    title = models.CharField(max_length=200)
    status = models.CharField(max_length=20)

    def clean(self):
        if self.status not in ("DRAFT", "PUBLISHED"):
            raise ValidationError("Invalid status")

# 2. serializers.py (ДУБЛИРОВАНИЕ!)
class ArticleSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    status = serializers.ChoiceField(choices=["DRAFT", "PUBLISHED"])

# 3. forms.py (ОПЯТЬ ДУБЛИРОВАНИЕ!)
class ArticleForm(forms.ModelForm):
    class Meta:
        model = Article
        fields = "__all__"

    def clean_status(self):
        # И так во всех проектах по 100 раз...
        pass

# Как сделано с Django NOVA:

# 1. schema.py (ЕДИНСТВЕННЫЙ ИСТОЧНИК ПРАВДЫ)
from pydantic import BaseModel

class ArticleSchema(BaseModel):
    title: str
    status: Literal["DRAFT", "PUBLISHED"]

# 2. models.py (ВСЁ, БОЛЬШЕ НИЧЕГО НЕ НУЖНО)
class Article(NovaModel):
    title = models.CharField(max_length=200)
    status = models.CharField(max_length=20)

    _nova_config = NovaConfig(
        pydantic_schema=ArticleSchema,
        cache_enabled=True, 
        strict_validation=True
    )

# Любой вызов article.save() автоматически прогоняется через ArticleSchema.
# Forms и API генерируются из схемы автоматически.


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
https://test.pypi.org/project/django-nova/0.1.0/



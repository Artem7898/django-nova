from django.db import models
from pydantic import BaseModel, field_validator

# Импортируем после того, как pytest-django сам настроил Django
from nova import NovaConfig, NovaModel

# --- СХЕМА И МОДЕЛЬ ДЛЯ ТЕСТА ---

class BenchmarkSchema(BaseModel):
    name: str
    h_index: int = 0

    @field_validator("h_index")
    @classmethod
    def validate_h_index(cls, v: int) -> int:
        if v < 0:
            raise ValueError("h-index cannot be negative")
        return v


class BenchmarkResearcher(NovaModel):
    name = models.CharField(max_length=300)
    h_index = models.IntegerField(default=0)

    _nova_config = NovaConfig(
        pydantic_schema=BenchmarkSchema,
        cache_enabled=True,
        strict_validation=True
    )

    class Meta:
        app_label = "nova"
        managed = False


# --- БЕНЧМАРКИ ---

def test_pydantic_validation_speed(benchmark):
    """Чистый замер скорости Pydantic-валидации."""

    def run_validation():
        return BenchmarkSchema(name="Artem Alimpiev", h_index=42)

    result = benchmark(run_validation)
    assert result.h_index == 42


def test_nova_model_init_speed(benchmark):
    """Замер скорости инициализации NovaModel."""

    def create_model_instance():
        return BenchmarkResearcher(name="Test Performance", h_index=10)

    result = benchmark(create_model_instance)
    assert result.name == "Test Performance"
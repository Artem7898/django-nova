"""Performance tests for django-nova validation layers."""

from __future__ import annotations

from typing import Any

from django.db import models
from pydantic import BaseModel

from nova.typing.models import NovaConfig, NovaModel


# 1. Type parameter definitions for standard Pydantic Baseline
class BenchmarkSchema(BaseModel):
    name: str
    h_index: int = 0


class BenchmarkResearcher(NovaModel):
    name: models.CharField[str, str] = models.CharField(max_length=300)
    h_index: models.IntegerField[int | str, int] = models.IntegerField(default=0)

    class Meta:  # type: ignore[reportIncompatibleVariableOverride]
        app_label = "nova"
        managed = False

    _nova_config: Any = NovaConfig(pydantic_schema=BenchmarkSchema, cache_enabled=True)  # type: ignore[reportIncompatibleVariableOverride]


# FIX: Explicitly type-hint pytest-benchmark fixtures using Any or specialized types
def test_pydantic_pure_perf(benchmark: Any) -> None:
    """Benchmark pure Pydantic validation."""

    def run() -> None:
        BenchmarkSchema(name="Artem", h_index=42)

    benchmark(run)


def test_nova_model_full_perf(benchmark: Any) -> None:
    """Benchmark full NovaModel instantiation cycle."""

    def run() -> None:
        # FIX: Explicitly cast type references so member access evaluates reliably
        result = BenchmarkResearcher(name="Artem", h_index=42)
        assert result.h_index == 42

    benchmark(run)

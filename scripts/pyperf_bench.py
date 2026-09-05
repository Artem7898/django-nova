"""pyperf benchmark for Pydantic and Django Nova model construction."""

from __future__ import annotations

from typing import Any

import django
import pyperf
from django.conf import settings
from django.db import models
from pydantic import BaseModel, field_validator

from nova.typing.models import NovaConfig, NovaModel

if not settings.configured:
    settings.configure(
        SECRET_KEY="bench-secret-key",
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "nova",
        ],
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )

django.setup()


class BenchSchema(BaseModel):
    """Pydantic schema used for benchmark measurements."""

    name: str
    h_index: int = 0

    @field_validator("h_index")
    @classmethod
    def validate_h_index(cls, value: int) -> int:
        """Reject negative h-index values."""
        if value < 0:
            raise ValueError("h-index cannot be negative")
        return value


class BenchNovaModel(NovaModel):
    """Django Nova model used for benchmark measurements."""

    name: models.CharField[Any, Any] = models.CharField(max_length=300)
    h_index: models.IntegerField[Any, Any] = models.IntegerField(default=0)

    class Meta(NovaModel.Meta):
        app_label = "nova"
        managed = False

    _nova_config = NovaConfig(
        pydantic_schema=BenchSchema,
        cache_enabled=True,
    )


class PlainDjangoModel(models.Model):
    """Plain Django model used as a baseline."""

    name: models.CharField[Any, Any] = models.CharField(max_length=300)
    h_index: models.IntegerField[Any, Any] = models.IntegerField(default=0)

    class Meta:
        app_label = "nova"
        managed = False


def bench_pydantic() -> BenchSchema:
    """Benchmark plain Pydantic model construction."""
    return BenchSchema(name="Artem", h_index=42)


def bench_django() -> PlainDjangoModel:
    """Benchmark plain Django model construction."""
    return PlainDjangoModel(name="Artem", h_index=42)


def bench_nova() -> BenchNovaModel:
    """Benchmark Django Nova model construction."""
    return BenchNovaModel(name="Artem", h_index=42)


def main() -> None:
    """Run all model-construction benchmarks."""
    runner = pyperf.Runner()

    # pyperf's installed type information does not fully describe
    # Runner.bench_func. Keep this third-party boundary local instead
    # of weakening Pyright for the project.
    bench_func = runner.bench_func

    bench_func(
        "pydantic_model_initialization",
        bench_pydantic,
    )

    bench_func(
        "plain_django_model_initialization",
        bench_django,
    )

    bench_func(
        "nova_model_initialization",
        bench_nova,
    )


if __name__ == "__main__":
    main()

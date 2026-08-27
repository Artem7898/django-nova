"""pyperf benchmark for Pydantic and Django Nova model construction."""

from __future__ import annotations

import os
import sys

import pyperf

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, ".."))
src_dir = os.path.join(project_root, "src")

if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

# Django must be imported after the local src/ path is configured.
import django  # noqa: E402
from django.conf import settings  # noqa: E402

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

# These imports intentionally happen after django.setup().
from django.db import models  # noqa: E402
from pydantic import BaseModel, field_validator  # noqa: E402

from nova.typing.models import NovaConfig, NovaModel  # noqa: E402


class BenchSchema(BaseModel):
    """Pydantic schema used by the benchmark."""

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
    """Django Nova model used by the benchmark."""

    name = models.CharField(max_length=300)
    h_index = models.IntegerField(default=0)

    class Meta:
        app_label = "nova"
        managed = False

    _nova_config = NovaConfig(
        pydantic_schema=BenchSchema,
        cache_enabled=True,
    )


class PlainDjangoModel(models.Model):
    """Plain Django model used as the baseline."""

    name = models.CharField(max_length=300)
    h_index = models.IntegerField(default=0)

    class Meta:
        app_label = "nova"
        managed = False


def bench_pydantic() -> BenchSchema:
    """Benchmark Pydantic model construction."""
    return BenchSchema(name="Artem", h_index=42)


def bench_django() -> PlainDjangoModel:
    """Benchmark plain Django model construction."""
    return PlainDjangoModel(name="Artem", h_index=42)


def bench_nova() -> BenchNovaModel:
    """Benchmark Django Nova model construction."""
    return BenchNovaModel(name="Artem", h_index=42)


if __name__ == "__main__":
    runner = pyperf.Runner()

    runner.bench_func(
        "pydantic_model_initialization",
        bench_pydantic,
    )

    runner.bench_func(
        "plain_django_model_initialization",
        bench_django,
    )

    runner.bench_func(
        "nova_model_initialization",
        bench_nova,
    )

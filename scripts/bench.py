"""
Stable baseline benchmark script.
Disables GC to show real algorithmic performance without Python's pauses.
Run: uv run python scripts/bench.py
"""

import gc
import os
import sys
import timeit

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(current_dir, "..")

sys.path.insert(0, os.path.join(project_root, "src"))

import django  # noqa: E402
from django.conf import settings  # noqa: E402

if not settings.configured:
    settings.configure(
        SECRET_KEY="bench-secret-key",
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "nova",
        ],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        USE_I18N=False,  # Отключаем i18n overhead
        USE_TZ=False,  # Отключаем timezone overhead
    )
    django.setup()

from django.db import models  # noqa: E402
from pydantic import BaseModel, field_validator  # noqa: E402

from nova.typing.models import NovaConfig, NovaModel  # noqa: E402

# ============================================================================
# Pure Pydantic (baseline)
# ============================================================================


class PurePydanticSchema(BaseModel):
    name: str
    h_index: int = 0

    @field_validator("h_index")
    @classmethod
    def validate_h_index(cls, v: int) -> int:
        if v < 0:
            raise ValueError("h-index cannot be negative")
        return v


# ============================================================================
# Pure Django (ORM baseline)
# ============================================================================


class PureDjangoModel(models.Model):
    name = models.CharField(max_length=300)
    h_index = models.IntegerField(default=0)

    class Meta:
        app_label = "nova"
        managed = False


# ============================================================================
# Nova (Django + Nova layers)
# ============================================================================


class BenchNovaModel(NovaModel):
    name = models.CharField(max_length=300)
    h_index = models.IntegerField(default=0)

    class Meta:
        app_label = "nova"
        managed = False

    _nova_config = NovaConfig(pydantic_schema=PurePydanticSchema, cache_enabled=True)


# ============================================================================
# Benchmark
# ============================================================================


gc.disable()

ITERATIONS = 100_000

print(f"Running {ITERATIONS:,} iterations (GC Disabled)...\n")

# 1. Pure Pydantic
pydantic_time = timeit.timeit(
    "PurePydanticSchema(name='Artem', h_index=42)",
    globals=globals(),
    number=ITERATIONS,
)

# 2. Pure Django
django_time = timeit.timeit(
    "PureDjangoModel(name='Artem', h_index=42)",
    globals=globals(),
    number=ITERATIONS,
)

# 3. Nova (Django + Nova)
nova_time = timeit.timeit(
    "BenchNovaModel(name='Artem', h_index=42)",
    globals=globals(),
    number=ITERATIONS,
)

# 4. Nova → Pydantic conversion (отдельно)
nova_obj = BenchNovaModel(name="Artem", h_index=42)
to_pydantic_time = timeit.timeit(
    "nova_obj.to_pydantic()",
    globals=globals(),
    number=ITERATIONS,
)

# ============================================================================
# Results
# ============================================================================


pydantic_per_iter = (pydantic_time / ITERATIONS) * 1_000_000
django_per_iter = (django_time / ITERATIONS) * 1_000_000
nova_per_iter = (nova_time / ITERATIONS) * 1_000_000
to_pydantic_per_iter = (to_pydantic_time / ITERATIONS) * 1_000_000

print("=" * 60)
print("BASELINE COMPARISON")
print("=" * 60)
print(f"Pure Pydantic:       {pydantic_per_iter:7.3f} µs/iter")
print(
    f"Pure Django Model:   {django_per_iter:7.3f} µs/iter  (+{django_per_iter - pydantic_per_iter:6.3f} µs vs Pydantic)"
)
print(
    f"Nova Model:          {nova_per_iter:7.3f} µs/iter  (+{nova_per_iter - django_per_iter:6.3f} µs vs Django)"
)
print()
print("=" * 60)
print("OVERHEAD ANALYSIS")
print("=" * 60)
print(
    f"Django overhead:     +{django_per_iter - pydantic_per_iter:6.3f} µs  ({(django_per_iter / pydantic_per_iter):.2f}x vs Pydantic)"
)
print(
    f"Nova overhead:       +{nova_per_iter - django_per_iter:6.3f} µs  ({(nova_per_iter / django_per_iter):.2f}x vs Django)"
)
print(
    f"Total overhead:      +{nova_per_iter - pydantic_per_iter:6.3f} µs  ({(nova_per_iter / pydantic_per_iter):.2f}x vs Pydantic)"
)
print()
print("=" * 60)
print("CONVERSION COST")
print("=" * 60)
print(f"Nova → Pydantic:     {to_pydantic_per_iter:7.3f} µs/iter  (per to_pydantic() call)")
print("=" * 60)

gc.enable()

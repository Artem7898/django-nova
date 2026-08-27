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
# sys.path.insert(0, os.path.join(project_root, "tests"))


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
    )
    django.setup()


from django.db import models  # noqa: E402
from pydantic import BaseModel, field_validator  # noqa: E402

from nova.typing.models import NovaConfig, NovaModel  # noqa: E402


class BenchSchema(BaseModel):
    name: str
    h_index: int = 0

    @field_validator("h_index")
    @classmethod
    def validate_h_index(cls, v: int) -> int:
        if v < 0:
            raise ValueError("h-index cannot be negative")
        return v


class BenchNovaModel(NovaModel):
    name = models.CharField(max_length=300)
    h_index = models.IntegerField(default=0)

    class Meta:
        app_label = "nova"
        managed = False

    _nova_config = NovaConfig(pydantic_schema=BenchSchema, cache_enabled=True)


gc.disable()

ITERATIONS = 100_000

print(f"Running {ITERATIONS:,} iterations (GC Disabled)...\n")


pydantic_time = timeit.timeit(
    "BenchSchema(name='Artem', h_index=42)", globals=globals(), number=ITERATIONS
)


nova_time = timeit.timeit(
    "BenchNovaModel(name='Artem', h_index=42)", globals=globals(), number=ITERATIONS
)


pydantic_per_iter = (pydantic_time / ITERATIONS) * 1_000_000
nova_per_iter = (nova_time / ITERATIONS) * 1_000_000
overhead = nova_per_iter / pydantic_per_iter


print("=" * 50)
print(f"Pure Pydantic:     {pydantic_per_iter:.3f} µs/iter")
print(f"NovaModel (Full):  {nova_per_iter:.3f} µs/iter")
print(f"Overhead Ratio:    {overhead:.2f}x")
print(f"Absolute Overhead: +{nova_per_iter - pydantic_per_iter:.3f} µs")
print("=" * 50)


gc.enable()

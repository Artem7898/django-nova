"""Explicit benchmark: uv run pytest -q -s benchmarks/queryset_cache_cost.py.

Outside testpaths and without the test_ filename prefix, this module is run
only when requested explicitly. No latency thresholds affect CI correctness.
"""

import gc
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import statistics
import subprocess
import time
import tracemalloc
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import django
import pytest
from django.db import connection, models
from django.test.utils import CaptureQueriesContext
from tests.cache.test_related_invalidation import relations as relations
from tests.integration.cache._queryset_process_worker import remote_backend

from nova.cache.backends.memory import MemoryCacheBackend
from nova.cache.generation import generation_key, generation_scope
from nova.cache.queryset_cache import QuerySetCache
from nova.cache.result_snapshot import snapshot_rows

pytestmark = pytest.mark.benchmark
SCENARIOS = ("plain", "select_related", "prefetch_related", "json")
OPERATIONS = ("orm", "snapshot", "backend_get", "cache_hit", "cache_fill")


def configuration():
    sizes = sorted({int(item) for item in os.getenv("NOVA_BENCH_SIZES", "1,10,100").split(",")})
    backends = os.getenv("NOVA_BENCH_BACKENDS", "memory,redis,memcached").split(",")
    scenarios = os.getenv("NOVA_BENCH_SCENARIOS", ",".join(SCENARIOS)).split(",")
    rounds = int(os.getenv("NOVA_BENCH_ROUNDS", "25"))
    warmups = int(os.getenv("NOVA_BENCH_WARMUPS", "3"))
    memory_samples = int(os.getenv("NOVA_BENCH_MEMORY_SAMPLES", "3"))
    assert sizes and min(sizes) > 0, "NOVA_BENCH_SIZES must contain positive integers"
    assert rounds >= 5 and warmups >= 1 and memory_samples >= 1
    assert backends and set(backends) <= {"memory", "redis", "memcached"}
    assert scenarios and set(scenarios) <= set(SCENARIOS)
    for backend, variable in (
        ("redis", "NOVA_TEST_REDIS_URL"),
        ("memcached", "NOVA_TEST_MEMCACHED_SERVER"),
    ):
        if backend in backends:
            assert os.getenv(variable), f"Set {variable} for the selected {backend} backend"
    return {
        "sizes": sizes,
        "backends": backends,
        "scenarios": scenarios,
        "rounds": rounds,
        "warmups": warmups,
        "memory_samples": memory_samples,
        "tags_per_article": 3,
    }


def seed_rows(case, size):
    profiles = case.Profile.objects.bulk_create(
        [case.Profile(label=f"bench-profile-{i}") for i in range(size)]
    )
    authors = case.Author.objects.bulk_create(
        [
            case.Author(name=f"bench-author-{i}", profile=profile)
            for i, profile in enumerate(profiles)
        ]
    )
    articles = case.Article.objects.bulk_create(
        [
            case.Article(title=f"bench-article-{i}", author=author)
            for i, author in enumerate(authors)
        ]
    )
    tags = case.Tag.objects.bulk_create([case.Tag(name=f"bench-tag-{i}") for i in range(3)])
    through = case.Article.tags.through
    through.objects.bulk_create(
        [through(article_id=row.pk, tag_id=tag.pk) for row in articles for tag in tags]
    )
    return [row.pk for row in articles]


def make_query(case, primary_keys, scenario):
    query = case.Article.objects.filter(pk__in=primary_keys).order_by("pk")
    if scenario == "select_related":
        return query.select_related("author__profile")
    if scenario == "prefetch_related":
        return query.prefetch_related("tags")
    if scenario == "json":
        payload = {"items": [{"code": f"sku-{i}", "flags": [True, False]} for i in range(8)]}
        return query.annotate(payload=models.Value(payload, output_field=models.JSONField()))
    return query


def fingerprint(rows, scenario):
    result = []
    for row in rows:
        extra = None
        if scenario == "select_related":
            extra = (row.author.name, row.author.profile.label)
        elif scenario == "prefetch_related":
            extra = tuple(sorted(tag.name for tag in row.tags.all()))
        elif scenario == "json":
            extra = json.dumps(row.payload, sort_keys=True)
        result.append((row.pk, row.title, extra))
    return result


def mutate_result(rows, scenario):
    rows[0].title = "caller-only"
    if scenario == "select_related":
        rows[0].author.profile.label = "caller-only"
    elif scenario == "prefetch_related":
        rows[0].tags.all()[0].name = "caller-only"
    elif scenario == "json":
        rows[0].payload["items"][0]["flags"].append("caller-only")


@contextmanager
def backend_for(name):
    if name == "memory":
        yield MemoryCacheBackend(maxsize=100, ttl=3600)
    else:
        variable = "NOVA_TEST_REDIS_URL" if name == "redis" else "NOVA_TEST_MEMCACHED_SERVER"
        with remote_backend(
            {
                "backend": name,
                "address": os.environ[variable],
                "namespace": f"nova-cost:{uuid4().hex}",
            }
        ) as backend:
            yield backend


def measure(function, before, rounds, warmups, memory_samples):
    for _ in range(warmups):
        before()
        value = function()
        del value

    wall_ns, cpu_ns = [], []
    # GC keeps its normal state. Tracing allocations is NOT enabled here.
    for _ in range(rounds):
        before()
        cpu_start = time.process_time_ns()
        start = time.perf_counter_ns()
        value = function()
        wall_elapsed = time.perf_counter_ns() - start
        cpu_elapsed = time.process_time_ns() - cpu_start
        wall_ns.append(wall_elapsed)
        cpu_ns.append(cpu_elapsed)
        del value  # Result destruction is outside the measured call.

    peaks, retained = [], []
    for _ in range(memory_samples):
        gc.collect()
        before()
        tracemalloc.start()
        try:
            initial, _ = tracemalloc.get_traced_memory()
            tracemalloc.reset_peak()
            value = function()
            current, peak = tracemalloc.get_traced_memory()
            peaks.append(max(0, peak - initial))
            retained.append(max(0, current - initial))  # Includes the still-live result.
            del value
        finally:
            tracemalloc.stop()

    return {
        "wall_ns": wall_ns,
        "cpu_ns": cpu_ns,
        "median_ms": statistics.median(wall_ns) / 1_000_000,
        "sample_p95_ms": sorted(wall_ns)[math.ceil(0.95 * len(wall_ns)) - 1] / 1_000_000,
        "median_cpu_ms": statistics.median(cpu_ns) / 1_000_000,
        "peak_python_bytes": max(peaks),
        "peak_python_samples": peaks,
        "retained_python_bytes": statistics.median(retained),
    }


def check_case(cache, query, scenario, expected):
    cached = cache.get_or_set(query)
    assert fingerprint(cached, scenario) == expected
    with CaptureQueriesContext(connection) as captured:
        hit = cache.get(query)
        assert hit is not None, "Cache fill was not stored (check backend errors/item-size limits)"
        assert fingerprint(hit, scenario) == expected
    assert len(captured) == 0, "A supposed cache hit executed SQL"
    mutate_result(cached, scenario)
    with CaptureQueriesContext(connection) as captured:
        assert fingerprint(cache.get_or_set(query), scenario) == expected
    assert len(captured) == 0, "The isolation check must also remain a cache hit"


def measure_case(case, backend, name, primary_keys, scenario, config, result_keys):
    query = make_query(case, primary_keys, scenario)
    with CaptureQueriesContext(connection) as captured:
        materialized = list(query.all())
        expected = fingerprint(materialized, scenario)
    assert len(materialized) == len(primary_keys)
    sql_count = len(captured)
    assert sql_count == (2 if scenario == "prefetch_related" else 1)
    cache = QuerySetCache(backend=backend, ttl=3600)
    key = cache._generate_key(query)[0]
    result_keys.add(key)
    check_case(cache, query, scenario, expected)

    operations = {
        "orm": lambda: list(query.all()),  # all() must create a fresh unevaluated clone.
        "snapshot": lambda: snapshot_rows(materialized),
        "backend_get": lambda: backend.get(key),
        "cache_hit": lambda: cache.get_or_set(query),
        "cache_fill": lambda: cache.get_or_set(query),
    }
    records = []
    for operation in OPERATIONS:
        before = (lambda: backend.delete(key)) if operation == "cache_fill" else (lambda: None)
        before()
        with CaptureQueriesContext(connection) as captured:
            assert fingerprint(operations[operation](), scenario) == expected
        expected_sql = sql_count if operation in {"orm", "cache_fill"} else 0
        assert len(captured) == expected_sql, f"{operation} measured the wrong execution path"
        result = measure(
            operations[operation],
            before,
            config["rounds"],
            config["warmups"],
            config["memory_samples"],
        )
        result.update(
            {
                "backend": name,
                "scenario": scenario,
                "rows": len(primary_keys),
                "operation": operation,
                "validated_sql_count": expected_sql,
            }
        )
        records.append(result)

    # Check the entry survived the timed and memory phases with correct data.
    with CaptureQueriesContext(connection) as captured:
        final = cache.get(query)
        assert final is not None and fingerprint(final, scenario) == expected
    assert len(captured) == 0
    return records


def environment():
    from nova.cache import queryset_cache, result_snapshot

    versions = {}
    for package in ("Django", "pytest", "redis", "pymemcache", "psycopg"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    git = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    sources = {}
    source_root = Path(queryset_cache.__file__).parent
    for path in sorted(source_root.rglob("*.py")):
        sources[path.relative_to(source_root).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return {
        "python": platform.python_version(),
        "django": django.get_version(),
        "platform": platform.platform(),
        "versions": versions,
        "database_vendor": connection.vendor,
        "database_version": connection.get_database_version(),
        "git_commit": git.stdout.strip() if git.returncode == 0 else None,
        "cache_source_sha256": sources,
        "snapshot_module": result_snapshot.__name__,
        "gc_enabled": gc.isenabled(),
        "gc_thresholds": gc.get_threshold(),
        "perf_counter_resolution_seconds": time.get_clock_info("perf_counter").resolution,
    }


def write_report(report, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def print_case(records):
    first = records[0]
    parts = " ".join(f"{r['operation']}={r['median_ms']:.3f}ms" for r in records)
    hit = next(r for r in records if r["operation"] == "cache_hit")
    print(
        f"{first['backend']:9} {first['scenario']:16} rows={first['rows']:5} "
        f"{parts} hit_peak={hit['peak_python_bytes'] / 1024:.1f}KiB",
        flush=True,
    )


def test_measure_queryset_cache_cost(relations):
    assert not tracemalloc.is_tracing(), "Run without an outer allocation profiler"
    assert not connection.in_atomic_block and connection.get_autocommit()
    config = configuration()
    primary_keys = seed_rows(relations, max(config["sizes"]))
    started = datetime.now(UTC)
    default_name = f"/tmp/nova-cache-cost-{started.strftime('%Y%m%dT%H%M%S')}.json"
    output = Path(os.getenv("NOVA_BENCH_OUTPUT", default_name)).expanduser().resolve()
    report = {
        "schema_version": 1,
        "started_utc": started.isoformat(),
        "complete": False,
        "environment": environment(),
        "configuration": config,
        "measurements": [],
    }
    try:
        for name in config["backends"]:
            with backend_for(name) as backend:
                result_keys = set()
                try:
                    for size in config["sizes"]:
                        for scenario in config["scenarios"]:
                            records = measure_case(
                                relations,
                                backend,
                                name,
                                primary_keys[:size],
                                scenario,
                                config,
                                result_keys,
                            )
                            report["measurements"].extend(records)
                            print_case(records)
                finally:
                    for key in result_keys:
                        backend.delete(key)
                    # Exact keys in our unique namespace only; never flush a server.
                    related_models = (
                        relations.Article,
                        relations.Author,
                        relations.Profile,
                        relations.Tag,
                        relations.Article.tags.through,
                    )
                    for model in related_models:
                        for database in ("*", "default"):
                            backend.delete(
                                generation_key(generation_scope(model._meta.model_name, database))
                            )
        report["complete"] = True
    finally:
        report["finished_utc"] = datetime.now(UTC).isoformat()
        write_report(report, output)
        print(f"Benchmark report: {output}", flush=True)
    assert report["complete"]

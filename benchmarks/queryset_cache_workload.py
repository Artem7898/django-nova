"""Explicit paired mixed-workload benchmark; no CI latency thresholds.

Run with pytest -q -s benchmarks/queryset_cache_workload.py. Requires the
existing queryset_cache_cost.py benchmark and the cache integration fixtures.
Set NOVA_MIX_DIAGNOSTICS=1 to record GC and phase intervals separately from
ordinary performance runs, or =gc for GC, operation boundaries and SQL counts
only. GC remains enabled and thresholds are not changed.
"""

import hashlib
import itertools
import math
import os
import random
import statistics
import time
import tracemalloc
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from benchmarks.queryset_cache_cost import (
    SCENARIOS,
    environment,
    fingerprint,
    make_query,
    seed_rows,
    write_report,
)
from django.db import connection, transaction
from django.db.models.signals import m2m_changed, post_delete, post_save
from tests.cache.test_related_invalidation import relations as relations
from tests.integration.cache._queryset_process_worker import remote_backend
from tests.integration.cache.test_queryset_remote_backends import TrackedBackend

from nova.cache import invalidation
from nova.cache.backends.memory import MemoryCacheBackend
from nova.cache.queryset_cache import QuerySetCache

pytestmark = pytest.mark.benchmark


def configuration():
    def values(variable, default, convert):
        return list(
            dict.fromkeys(convert(x.strip()) for x in os.getenv(variable, default).split(","))
        )

    config = {
        "rows": int(os.getenv("NOVA_MIX_ROWS", "100")),
        "reads": int(os.getenv("NOVA_MIX_READS", "100")),
        "repeats": int(os.getenv("NOVA_MIX_REPEATS", "4")),
        "seed": int(os.getenv("NOVA_MIX_SEED", "20260920")),
        "hot_fractions": values("NOVA_MIX_HIT_RATES", "0,0.5,0.8,0.95,1", float),
        "write_every": values("NOVA_MIX_WRITE_EVERY", "0,10,50", int),
        "backends": values("NOVA_MIX_BACKENDS", "redis,memcached", str),
        "scenarios": values("NOVA_MIX_SCENARIOS", "prefetch_related", str),
        "ttl_seconds": 3600,
        "diagnostics": os.getenv("NOVA_MIX_DIAGNOSTICS", "0"),
        "slow_ms": float(os.getenv("NOVA_MIX_SLOW_MS", "20")),
    }
    modes = {"0": "timing", "1": "diagnostic", "gc": "gc_only"}
    assert config["diagnostics"] in modes, "NOVA_MIX_DIAGNOSTICS must be 0, 1 or gc"
    config["run_mode"] = modes[config["diagnostics"]]
    config["record_phases"] = config["diagnostics"] == "1"
    config["diagnostics"] = config["diagnostics"] != "0"
    assert math.isfinite(config["slow_ms"]) and config["slow_ms"] > 0
    assert config["rows"] > 0 and config["reads"] >= 20 and config["repeats"] >= 2
    assert all(math.isfinite(h) and 0 <= h <= 1 for h in config["hot_fractions"])
    assert all(n == 0 or 1 <= n <= config["reads"] for n in config["write_every"])
    assert set(config["backends"]) <= {"memory", "redis", "memcached"}
    assert set(config["scenarios"]) <= set(SCENARIOS)
    for name, variable in (
        ("redis", "NOVA_TEST_REDIS_URL"),
        ("memcached", "NOVA_TEST_MEMCACHED_SERVER"),
    ):
        if name in config["backends"]:
            assert os.getenv(variable), f"Set {variable} for the selected {name} backend"
    return config


def make_trace(reads, hot_fraction, write_every, seed):
    """Hot query 0 is primed; all other groups are read exactly once."""
    hot_reads = round(reads * hot_fraction)
    groups = [0] * hot_reads + list(range(1, reads - hot_reads + 1))
    random.Random(seed).shuffle(groups)
    trace = []
    for index, group in enumerate(groups, 1):
        if write_every and index % write_every == 0:
            trace.append({"kind": "write"})
        trace.append({"kind": "read", "group": group})
    return trace


def expected_hits(trace):
    """Upper bound for this trace with successful storage and no eviction."""
    hot_is_cached, hits = True, 0
    for operation in trace:
        if operation["kind"] == "write":
            hot_is_cached = False
        elif operation["group"] == 0:
            hits += int(hot_is_cached)
            hot_is_cached = True
    return hits


@contextmanager
def connected_writer(model, cache):
    """Disconnect this arm's receivers before closing its backend clients."""
    registries = (invalidation._CONNECTED_SIGNALS, invalidation._CONNECTED_M2M_SIGNALS)
    previous = [set(registry) for registry in registries]
    receivers = []
    with pytest.MonkeyPatch.context() as patcher:
        for signal in (post_save, post_delete, m2m_changed):
            original = signal.connect

            def connect(receiver, *args, _signal=signal, _connect=original, **kwargs):
                receivers.append((_signal, receiver, kwargs.get("sender")))
                return _connect(receiver, *args, **kwargs)

            patcher.setattr(signal, "connect", connect)
        try:
            invalidation.connect_invalidation(model, cache=cache)
            yield
        finally:
            for signal, receiver, sender in receivers:
                signal.disconnect(receiver, sender=sender)
            for registry, before in zip(registries, previous, strict=True):
                registry.intersection_update(before)


@contextmanager
def cache_pair(name, config):
    """Remote writer has an independent client and never reads query results."""
    if name == "memory":
        backend = MemoryCacheBackend(maxsize=config["reads"] + 10, ttl=config["ttl_seconds"])
        reader = QuerySetCache(backend=backend, ttl=config["ttl_seconds"])
        try:
            yield reader, QuerySetCache(_state=reader._state)
        finally:
            backend.clear()  # This private in-process backend belongs only to this arm.
        return
    variable = "NOVA_TEST_REDIS_URL" if name == "redis" else "NOVA_TEST_MEMCACHED_SERVER"
    spec = {
        "backend": name,
        "address": os.environ[variable],
        "namespace": f"nova-mix:{uuid4().hex}",
    }
    keys = set()
    with ExitStack() as resources:
        raw_reader = resources.enter_context(remote_backend(spec))
        raw_writer = resources.enter_context(remote_backend(spec))
        reader = QuerySetCache(backend=TrackedBackend(raw_reader, keys), ttl=config["ttl_seconds"])
        writer = QuerySetCache(backend=TrackedBackend(raw_writer, keys), ttl=config["ttl_seconds"])
        try:
            yield reader, writer
        finally:
            for key in keys:
                raw_reader.delete(key)


class SQLCounter:
    """Count statements without Django's SQL debug logging or query formatting."""

    def __init__(self, diagnostics=None):
        self.count = 0
        self.diagnostics = (
            diagnostics if diagnostics is not None and diagnostics.record_phases else None
        )

    def __call__(self, execute, sql, params, many, context):
        self.count += 1
        if self.diagnostics is not None:
            return self.diagnostics.call(
                "sql", "executemany" if many else "execute", execute, sql, params, many, context
            )
        return execute(sql, params, many, context)


def save_and_commit(row, title):
    with transaction.atomic(using=row._state.db):
        row.title = title
        row.save(update_fields=["title"])
    # on_commit callbacks have completed when the atomic block returns.


def assert_rows(rows, expected, scenario, group, title):
    actual = fingerprint(rows, scenario)
    wanted = expected[group]
    if group == 0:
        first, *rest = wanted
        wanted = [(first[0], title, first[2]), *rest]
    assert actual == wanted, "Mixed workload returned stale or incomplete query results"


def run_arm(case, name, mode, config, trace, queries, expected, scenario, caplog, diagnostics=None):
    initial_title = expected[0][0][1]
    write_pk = expected[0][0][0]
    # Fixture reset intentionally bypasses signals before this arm is connected.
    case.Article.objects.filter(pk=write_pk).update(title=initial_title)
    row = case.Article.objects.get(pk=write_pk)
    first_log = len(caplog.records)
    counter = SQLCounter(diagnostics)
    operations = []
    read_sql = 2 if scenario == "prefetch_related" else 1

    with ExitStack() as resources:
        if mode == "cache":
            reader, writer = resources.enter_context(cache_pair(name, config))
            resources.enter_context(connected_writer(case.Article, writer))

            def read(query):
                return reader.get_or_set(query)

        else:

            def read(query):
                return list(query)

        resources.enter_context(connection.execute_wrapper(counter))
        # Untimed warm-up includes a real commit, its invalidation, and a refill.
        read(queries[0].all())
        save_and_commit(row, initial_title)
        read(queries[0].all())
        before = counter.count
        warm = read(queries[0].all())
        assert counter.count - before == (0 if mode == "cache" else read_sql), (
            "Preflight failed: cache must store and serve a hot result"
        )
        assert_rows(warm, expected, scenario, 0, initial_title)
        del warm
        if diagnostics is not None and diagnostics.record_phases and mode == "cache":
            diagnostics.instrument_cache(reader, "reader")
            diagnostics.instrument_cache(writer, "writer")
        version, title = 0, initial_title
        for index, event in enumerate(trace):
            if event["kind"] == "write":
                version += 1
                title = f"mix-write-{version}"
                before = counter.count
                if diagnostics is None:
                    start = time.perf_counter_ns()
                    save_and_commit(row, title)
                    elapsed = time.perf_counter_ns() - start
                else:
                    _, start, end = diagnostics.measure(index, save_and_commit, row, title)
                    elapsed = end - start
                operations.append(
                    {
                        "kind": "write",
                        "elapsed_ns": elapsed,
                        "sql_count": counter.count - before,
                        "index": index,
                        "version": version,
                    }
                )
                if diagnostics is not None:
                    operations[-1].update(start_ns=start, end_ns=end)
                assert connection.get_autocommit() and not connection.in_atomic_block
                continue
            group = event["group"]
            query = queries[group].all()  # Fresh QuerySet; never reuse its result cache.
            before = counter.count
            if diagnostics is None:
                start = time.perf_counter_ns()
                rows = read(query)
                elapsed = time.perf_counter_ns() - start
            else:
                rows, start, end = diagnostics.measure(index, read, query)
                elapsed = end - start
            sql_count = counter.count - before
            assert sql_count in ({0, read_sql} if mode == "cache" else {read_sql})
            outcome = "orm" if mode == "orm" else "hit" if sql_count == 0 else "miss"
            operations.append(
                {
                    "kind": "read",
                    "elapsed_ns": elapsed,
                    "sql_count": sql_count,
                    "index": index,
                    "group": group,
                    "version": version,
                    "outcome": outcome,
                }
            )
            if diagnostics is not None:
                operations[-1].update(start_ns=start, end_ns=end)
            validation_before = counter.count
            assert_rows(rows, expected, scenario, group, title)
            assert counter.count == validation_before, "Validation caused lazy relation SQL"
            del rows, query
        # Also verify the changed hot row if the trace ended with only cold reads.
        final_rows = read(queries[0].all())
        assert_rows(final_rows, expected, scenario, 0, title)
        assert case.Article.objects.get(pk=write_pk).title == title
        if mode == "cache" and name != "memory":
            assert not writer._state.model_keys, "Writer must never prime query results"
    warnings = [
        r.getMessage()
        for r in caplog.records[first_log:]
        if r.name.startswith("nova.cache") and r.levelno >= 30
    ]
    assert not warnings, f"Cache failures invalidate this performance run: {warnings}"
    return operations


def distribution(operations):
    samples = [item["elapsed_ns"] for item in operations]
    if not samples:
        return {
            "count": 0,
            "mean_ms": None,
            "median_ms": None,
            "sample_p95_ms": None,
            "sql_count": 0,
            "total_ns": 0,
        }
    return {
        "count": len(samples),
        "mean_ms": statistics.fmean(samples) / 1_000_000,
        "median_ms": statistics.median(samples) / 1_000_000,
        "sample_p95_ms": sorted(samples)[math.ceil(0.95 * len(samples)) - 1] / 1_000_000,
        "sql_count": sum(item["sql_count"] for item in operations),
        "total_ns": sum(samples),
    }


def summarize(operations):
    reads = [op for op in operations if op["kind"] == "read"]
    writes = [op for op in operations if op["kind"] == "write"]
    hits = [op for op in reads if op["outcome"] == "hit"]
    misses = [op for op in reads if op["outcome"] == "miss"]
    return {
        "all": distribution(operations),
        "reads": distribution(reads),
        "writes": distribution(writes),
        "hits": distribution(hits),
        "misses": distribution(misses),
        "actual_hit_rate": len(hits) / len(reads) if hits or misses else None,
        "writes_per_100_reads": len(writes) / len(reads) * 100,
        "write_fraction_of_all_operations": len(writes) / len(operations),
    }


def finalize_case(record):
    summaries = {}
    for mode in ("orm", "cache"):
        operations = [op for pair in record["pairs"] for op in pair[mode]["operations"]]
        summaries[mode] = summarize(operations)
    orm, cache = summaries["orm"], summaries["cache"]
    record["summary"] = summaries
    record["read_speedup"] = orm["reads"]["mean_ms"] / cache["reads"]["mean_ms"]
    record["mixed_speedup"] = orm["all"]["mean_ms"] / cache["all"]["mean_ms"]
    record["expected_hit_rate_without_eviction"] = (
        sum(pair["expected_hits_without_eviction"] for pair in record["pairs"])
        / cache["reads"]["count"]
    )
    record["paired_mixed_speedups"] = [
        pair["orm"]["summary"]["all"]["mean_ms"] / pair["cache"]["summary"]["all"]["mean_ms"]
        for pair in record["pairs"]
    ]
    write_ms = cache["writes"]["mean_ms"]
    write_orm_ms = orm["writes"]["mean_ms"]
    record["write_mean_difference_ms"] = None if write_ms is None else write_ms - write_orm_ms
    write_label = "n/a" if write_ms is None else f"{write_ms:.3f}/{write_orm_ms:.3f}ms"
    print(
        f"{record['backend']:9} {record['scenario']:16} rows={record['rows']} "
        f"hot={record['hot_fraction']:.0%} writes/100r={cache['writes_per_100_reads']:.1f} "
        f"hit={cache['actual_hit_rate']:.1%} "
        f"read={cache['reads']['mean_ms']:.3f}/{orm['reads']['mean_ms']:.3f}ms(cache/orm) "
        f"write={write_label} "
        f"mixed={cache['all']['mean_ms']:.3f}/{orm['all']['mean_ms']:.3f}ms "
        f"speedup={record['mixed_speedup']:.2f}x "
        f"read_sql={cache['reads']['sql_count']}/{orm['reads']['sql_count']}",
        flush=True,
    )


def print_diagnostics(record):
    for pair in record["pairs"]:
        for mode in pair["order"]:
            reads = pair[mode]["diagnostics"]["reads"]
            slow_ns = reads["slow_total_ns"]
            gc_share = reads["slow_gc_overlap_ns"] / slow_ns if slow_ns else 0
            print(
                f"DIAG {record['backend']:9} {record['scenario']} rows={record['rows']} "
                f"hot={record['hot_fraction']:.0%} write_every={record['write_every']} "
                f"repeat={pair['repeat'] + 1} {mode:5} "
                f"slow={reads['slow_count']}/{reads['count']} "
                f"slow_with_gc={reads['slow_with_gc_count']} "
                f"gc_share_of_slow_time={gc_share:.1%} "
                f"p99={reads['sample_p99_ms']:.3f}ms max={reads['max_ms']:.3f}ms",
                flush=True,
            )


def test_measure_mixed_queryset_workload(relations, caplog):
    assert not tracemalloc.is_tracing(), "Run without allocation tracing"
    assert connection.get_autocommit() and not connection.in_atomic_block
    assert not connection.queries_logged, "Disable DEBUG and SQL debug cursor for timing"
    config = configuration()
    if config["diagnostics"]:
        import gc

        from benchmarks.workload_diagnostics import ArmDiagnostics

        assert gc.isenabled() and gc.get_threshold()[0] > 0, (
            "Run diagnostics with automatic GC enabled"
        )
    # One hot group plus disjoint cold groups: misses require no artificial deletes.
    group_count = 1 + max(
        config["reads"] - round(config["reads"] * h) for h in config["hot_fractions"]
    )
    preparation = (
        ArmDiagnostics(record_phases=config["record_phases"]) if config["diagnostics"] else None
    )
    with preparation if preparation is not None else nullcontext():
        primary_keys = seed_rows(relations, config["rows"] * group_count)
        groups = [
            primary_keys[start : start + config["rows"]]
            for start in range(0, len(primary_keys), config["rows"])
        ]
        queries = {
            scenario: [make_query(relations, group, scenario) for group in groups]
            for scenario in config["scenarios"]
        }
        expected = {
            scenario: [fingerprint(list(query.all()), scenario) for query in templates]
            for scenario, templates in queries.items()
        }
    started = datetime.now(UTC)
    output = (
        Path(
            os.getenv(
                "NOVA_MIX_OUTPUT",
                f"/tmp/nova-cache-workload-"
                f"{config['run_mode'] + '-' if config['diagnostics'] else ''}"
                f"{started.strftime('%Y%m%dT%H%M%S')}.json",
            )
        )
        .expanduser()
        .resolve()
    )
    report = {
        "schema_version": 2 if config["diagnostics"] else 1,
        "run_mode": config["run_mode"],
        "process_id": os.getpid(),
        "started_utc": started.isoformat(),
        "complete": False,
        "environment": environment(),
        "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "configuration": config,
        "method": {
            "load": "serial closed-loop; operation ratio, not prescribed writes/second",
            "hit_parameter": "fraction of hot-query requests before commit invalidation",
            "write": "Article.save(update_fields=['title']) inside atomic, including on_commit",
            "timing": "wall time of API call including SQL/commit/cache I/O; arithmetic means",
            "excluded": "setup, query construction, validation, result disposal, cleanup",
            "sql_counter": "execute_wrapper counter active during timings; excludes driver COMMIT",
            "warm_state": "database primed; hot result primed after untimed write; cold keys unseen",
            "cleanup": "fresh namespace per cache arm; exact tracked keys only, never flush",
        },
        "cases": [],
    }
    if config["diagnostics"]:
        report["diagnostics_pending"] = True
        report["diagnostics_sha256"] = hashlib.sha256(
            Path(__file__).with_name("workload_diagnostics.py").read_bytes()
        ).hexdigest()
        report["method"]["diagnostics"] = {
            "record_phases": config["record_phases"],
            "clock": "perf_counter_ns, process-local monotonic wall time",
            "gc": "process GC callbacks; unchanged enablement and thresholds",
            "scope": "whole arm: reset, warmup, reads/writes, validation, disposal and cleanup",
            "preparation": "separate GC scope for seeding, query templates and expected fingerprints",
            "postprocessing": "intersections and detailed summaries are built after all timed arms",
            "warning": "instrumentation changes allocation/timing; do not mix with timing-only runs",
        }
        if config["record_phases"]:
            report["method"]["diagnostics"].update(
                {
                    "sql": "execute/executemany wall time; excludes fetch, hydration and driver COMMIT",
                    "backend_io": "client calls and generation transport; includes driver/pool overhead",
                    "backend_api": "inclusive backend method time; overlaps serialization and client I/O",
                    "overlap": "GC overlaps phases; unions avoid counting nested spans twice",
                    "other": "read duration outside SQL/serializer/client/GC union, including probe overhead",
                }
            )
        else:
            report["method"]["diagnostics"].update(
                {
                    "sql": "simple statement counter only; no SQL timings or retained SQL/parameters",
                    "unobserved": "no serializer, backend or client wrappers; no phase spans",
                    "non_gc": "operation wall time outside observed GC, not CPU time or causal GC savings",
                }
            )
        print(
            f"{config['run_mode'].upper()} RUN: GC settings unchanged; "
            f"phase recording={'on' if config['record_phases'] else 'off'}; observer overhead remains.",
            flush=True,
        )
    matrix = list(
        itertools.product(
            config["backends"], config["scenarios"], config["hot_fractions"], config["write_every"]
        )
    )
    random.Random(config["seed"]).shuffle(matrix)
    print(
        "Arithmetic means; latency pairs are cache/ORM. "
        "hot=request mix before invalidation; hit=measured SQL-free reads. "
        "writes/100r counts writes per 100 reads, not writes per second.",
        flush=True,
    )
    pending_diagnostics = []
    try:
        for case_index, (name, scenario, fraction, every) in enumerate(matrix):
            record = {
                "backend": name,
                "scenario": scenario,
                "rows": config["rows"],
                "hot_fraction": fraction,
                "write_every": every,
                "pairs": [],
            }
            report["cases"].append(record)
            for repeat in range(config["repeats"]):
                seed = config["seed"] + repeat
                trace = make_trace(config["reads"], fraction, every, seed)
                order = ["orm", "cache"] if (case_index + repeat) % 2 == 0 else ["cache", "orm"]
                pair = {
                    "repeat": repeat,
                    "seed": seed,
                    "order": order,
                    "trace": trace,
                    "expected_hits_without_eviction": expected_hits(trace),
                }
                record["pairs"].append(pair)
                for mode in order:
                    diagnostics = (
                        ArmDiagnostics(record_phases=config["record_phases"])
                        if config["diagnostics"]
                        else None
                    )
                    with diagnostics if diagnostics is not None else nullcontext():
                        operations = run_arm(
                            relations,
                            name,
                            mode,
                            config,
                            trace,
                            queries[scenario],
                            expected[scenario],
                            scenario,
                            caplog,
                            diagnostics=diagnostics,
                        )
                    pair[mode] = {"operations": operations, "summary": summarize(operations)}
                    if diagnostics is not None:
                        pending_diagnostics.append((pair, mode, diagnostics))
                actual_hits = pair["cache"]["summary"]["hits"]["count"]
                assert actual_hits <= pair["expected_hits_without_eviction"], (
                    "More hits than allowed by the commit trace: invalidation may have failed"
                )
                if every == 0 and fraction == 1:
                    assert actual_hits == config["reads"], "Fully warm reference must stay hot"
            finalize_case(record)
            write_report(report, output)
        # Avoid retaining the large diagnostic dictionaries while later arms
        # are still measured: they would themselves change the GC workload.
        for pair, mode, diagnostics in pending_diagnostics:
            pair[mode]["diagnostics"] = diagnostics.annotate(
                pair[mode]["operations"],
                repeat=pair["repeat"],
                mode=mode,
                slow_ms=config["slow_ms"],
            )
        if preparation is not None:
            report["preparation_diagnostics"] = preparation.annotate(
                [], repeat=-1, mode="preparation", slow_ms=config["slow_ms"]
            )
        if config["diagnostics"]:
            report["diagnostics_pending"] = False
            for record in report["cases"]:
                print_diagnostics(record)
        report["complete"] = True
    finally:
        report["finished_utc"] = datetime.now(UTC).isoformat()
        write_report(report, output)
        print(f"Mixed workload report: {output}", flush=True)
    assert report["complete"]

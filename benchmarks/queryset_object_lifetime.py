"""Explicit object-lifetime experiment, NOT a performance benchmark.

Automatic GC stays enabled. Explicit full collections occur at labeled
checkpoints; no read latency is measured. No production cache code is patched.
"""

import gc
import os
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

import pytest
from benchmarks.object_lifetime import (
    WeakGraph,
    collect_checkpoint,
    observe_model_initialization,
    require_automatic_gc,
)
from benchmarks.queryset_cache_cost import (
    SCENARIOS,
    environment,
    fingerprint,
    make_query,
    seed_rows,
    write_report,
)
from benchmarks.queryset_cache_workload import SQLCounter, assert_rows, cache_pair
from benchmarks.workload_diagnostics import ArmDiagnostics
from django.db import connection
from tests.cache.test_related_invalidation import relations as relations

pytestmark = pytest.mark.benchmark


def configuration():
    config = {
        "rows": int(os.getenv("NOVA_LIFETIME_ROWS", "100")),
        "repeats": int(os.getenv("NOVA_LIFETIME_REPEATS", "3")),
        "backends": os.getenv("NOVA_LIFETIME_BACKENDS", "redis,memcached").split(","),
        "scenarios": os.getenv("NOVA_LIFETIME_SCENARIOS", "plain,prefetch_related").split(","),
        "modes": os.getenv("NOVA_LIFETIME_MODES", "orm,cache_fill").split(","),
    }
    assert config["rows"] > 0 and config["repeats"] > 0
    assert set(config["backends"]) <= {"redis", "memcached", "memory"}
    assert set(config["scenarios"]) <= set(SCENARIOS)
    assert set(config["modes"]) <= {"orm", "cache_fill", "cache_hit"}
    for backend, variable in (
        ("redis", "NOVA_TEST_REDIS_URL"),
        ("memcached", "NOVA_TEST_MEMCACHED_SERVER"),
    ):
        if backend in config["backends"]:
            assert os.getenv(variable), f"Set {variable} for {backend}"
    return config


def model_classes(case):
    return (case.Profile, case.Author, case.Article, case.Tag, case.Article.tags.through)


def project_expected(template, scenario, graph):
    query = template.all()
    rows = list(query)
    graph.capture(rows, query)
    return fingerprint(rows, scenario)


def preparation_probe(case, config):
    """Keep only PKs/fingerprints; observe actual seed/expected helper functions."""
    seed, templates_graph, projection = WeakGraph(), WeakGraph(), WeakGraph()
    baseline = gc.collect(2)
    with ArmDiagnostics() as observer:
        with observe_model_initialization(seed, model_classes(case), "seed"):
            primary_keys = seed_rows(case, config["rows"])
        seeded = collect_checkpoint(seed, "seed_returned_only_primary_keys")
        templates = {s: make_query(case, primary_keys, s) for s in config["scenarios"]}
        for template in templates.values():
            templates_graph.capture([], template)
        del template
        with observe_model_initialization(projection, model_classes(case), "expected"):
            expected = {s: project_expected(query, s, projection) for s, query in templates.items()}
        projected = collect_checkpoint(projection, "expected_fingerprints_still_alive")
        retained_templates = collect_checkpoint(templates_graph, "templates_still_alive")
        del templates
        dropped_templates = collect_checkpoint(templates_graph, "templates_dropped")
    return (
        primary_keys,
        expected,
        {
            "baseline_global_collected": baseline,
            "seed": {"checkpoint": seeded, "graph": seed.describe()},
            "expected": {"checkpoint": projected, "graph": projection.describe()},
            "templates": {
                "retained": retained_templates,
                "dropped": dropped_templates,
                "graph": templates_graph.describe(),
            },
            "gc": observer.annotate([], repeat=-1, mode="preparation", slow_ms=20),
        },
    )


def exercise_read(case, primary_keys, expected, name, mode, scenario, validate, graph):
    """Return only primitive snapshots after the resource-owning frame exits."""
    config = {"reads": 1, "ttl_seconds": 3600}
    checkpoints = {}
    counter = SQLCounter()
    validation_graph = WeakGraph()
    with ExitStack() as resources:
        if mode != "orm":
            reader, _writer = resources.enter_context(cache_pair(name, config))
        resources.enter_context(connection.execute_wrapper(counter))
        if mode == "cache_hit":
            # Priming is separate from the observed result; clear its cyclic garbage.
            reader.get_or_set(make_query(case, primary_keys, scenario))
            checkpoints["after_prime"] = collect_checkpoint(WeakGraph(), "after_prime")
        query = make_query(case, primary_keys, scenario)
        before = counter.count
        rows = list(query) if mode == "orm" else reader.get_or_set(query)
        sql_count = counter.count - before
        assert sql_count == (
            0 if mode == "cache_hit" else 2 if scenario == "prefetch_related" else 1
        )
        before_observation = counter.count
        graph.capture(rows, query)
        checkpoints["with_result_and_query"] = graph.snapshot("with_result_and_query")
        if validate:
            with observe_model_initialization(validation_graph, model_classes(case), "validation"):
                # Keep the projection alive across collection to test its ownership.
                projected = fingerprint(rows, scenario)
                assert projected == expected
                assert_rows(rows, [expected], scenario, 0, expected[0][1])
            graph.capture(rows, query)
        assert counter.count == before_observation, "Graph capture/validation caused lazy SQL"
        checkpoints["after_validation"] = graph.snapshot("after_validation")
        del rows
        checkpoints["after_drop_rows"] = graph.snapshot("after_drop_rows")
        del query
        checkpoints["with_cache_alive"] = collect_checkpoint(graph, "with_cache_alive")
        if validate:
            del projected
        checkpoints["after_projection_drop"] = collect_checkpoint(graph, "after_projection_drop")
        checkpoints["validation_allocations"] = collect_checkpoint(
            validation_graph, "validation_allocations"
        )
    return checkpoints, sql_count, validation_graph.describe()


def probe_read(case, primary_keys, expected, name, mode, scenario, validate, repeat):
    baseline = gc.collect(2)
    graph = WeakGraph()
    with ArmDiagnostics() as observer:
        checkpoints, sql_count, validation_graph = exercise_read(
            case, primary_keys, expected, name, mode, scenario, validate, graph
        )
        checkpoints["after_resource_cleanup"] = collect_checkpoint(graph, "after_resource_cleanup")
    return {
        "backend": name,
        "mode": mode,
        "scenario": scenario,
        "validate": validate,
        "repeat": repeat,
        "rows": len(primary_keys),
        "sql_count": sql_count,
        "baseline_global_collected": baseline,
        "checkpoints": checkpoints,
        "graph": graph.describe(),
        "validation_graph": validation_graph,
        "gc": observer.annotate([], repeat=repeat, mode=mode, slow_ms=20),
    }


def print_case(record):
    checkpoints = record["checkpoints"]
    retained = checkpoints["with_cache_alive"]
    cleaned = checkpoints["after_resource_cleanup"]["after"]
    print(
        f"LIFE {record['backend']:9} {record['scenario']:16} {record['mode']:10} "
        f"validate={record['validate']} repeat={record['repeat'] + 1} "
        f"observed={retained['before']['observed']} "
        f"after_drop={retained['before']['alive']} "
        f"after_gc_cache_alive={retained['after']['alive']} "
        f"after_cleanup_gc={cleaned['alive']}",
        flush=True,
    )


def test_queryset_object_lifetime(relations, caplog):
    require_automatic_gc()
    assert connection.get_autocommit() and not connection.in_atomic_block
    config = configuration()
    started = datetime.now(UTC)
    output = Path(os.getenv("NOVA_LIFETIME_OUTPUT", "/tmp/nova-queryset-lifetime.json"))
    report = {
        "schema_version": 1,
        "run_mode": "object_lifetime",
        "complete": False,
        "started_utc": started.isoformat(),
        "environment": environment(),
        "configuration": config,
        "method": {
            "latency": "not measured; forced full GC at explicitly labeled checkpoints",
            "automatic_gc": "enabled with unchanged thresholds; may collect before a snapshot",
            "scope": "weak references to models and QuerySets, not every allocated object",
            "containers": "builtin list/dict have no weakrefs; tracked through their model graph",
            "counts": "collected/uncollectable are process-wide, not ORM model counts",
            "survival": "observations, not timing/leak assertions; live owners may retain objects",
            "validation": "paired absent/present; fingerprint and assert_rows outside latency",
            "cache": "fresh namespace for each trial; original cache alive during first collection",
            "gc_totals": "includes explicit checkpoint collections; not natural collection frequency",
        },
        "cases": [],
    }
    first_log = len(caplog.records)
    try:
        primary_keys, expected, preparation = preparation_probe(relations, config)
        report["preparation"] = preparation
        for repeat in range(config["repeats"]):
            for scenario in config["scenarios"]:
                for name in config["backends"]:
                    modes = config["modes"] if repeat % 2 == 0 else config["modes"][::-1]
                    for mode in modes:
                        for validate in (False, True):
                            record = probe_read(
                                relations,
                                primary_keys,
                                expected[scenario],
                                name,
                                mode,
                                scenario,
                                validate,
                                repeat,
                            )
                            report["cases"].append(record)
                            print_case(record)
        warnings = [
            r.getMessage()
            for r in caplog.records[first_log:]
            if r.name.startswith("nova.cache") and r.levelno >= 30
        ]
        assert not warnings, f"Backend failures invalidate this experiment: {warnings}"
        report["complete"] = True
    finally:
        report["finished_utc"] = datetime.now(UTC).isoformat()
        write_report(report, output)
        print(f"Object lifetime report: {output}", flush=True)

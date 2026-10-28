"""One serializer variant per process; explicit experiment, no latency thresholds.

Use scripts/run_serializer_gc.py to isolate every variant and repeat. Database
access is limited to preparation. Timing never forces GC or observes weakrefs.
Lifetime mode records weak graphs and forces collections, but measures no call
latency. Neither mode patches the production serializer or cache backends.
"""

import gc
import hashlib
import os
import sys
import time
import tracemalloc
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from benchmarks.object_lifetime import WeakGraph, collect_checkpoint, require_automatic_gc
from benchmarks.queryset_cache_cost import (
    environment,
    fingerprint,
    make_query,
    seed_rows,
    write_report,
)
from benchmarks.serializer_hint_graph import paired_payloads
from benchmarks.workload_diagnostics import (
    ArmDiagnostics,
    intersect_intervals,
    interval_ns,
    latency_summary,
)
from django.db import connection, connections
from tests.cache.test_related_invalidation import relations as relations

from nova.cache.backends.serializers import PickleSerializer

pytestmark = pytest.mark.benchmark


def configuration():
    config = {
        "mode": os.getenv("NOVA_SERIAL_MODE", "timing"),
        "operation": os.getenv("NOVA_SERIAL_OPERATION", "loads"),
        "scenario": os.getenv("NOVA_SERIAL_SCENARIO", "prefetch_related"),
        "validate": os.getenv("NOVA_SERIAL_VALIDATE", "0"),
        "rows": int(os.getenv("NOVA_SERIAL_ROWS", "100")),
        "operations": int(os.getenv("NOVA_SERIAL_OPERATIONS", "400")),
        "warmups": int(os.getenv("NOVA_SERIAL_WARMUPS", "5")),
        "repeat": int(os.getenv("NOVA_SERIAL_REPEAT", "1")),
    }
    assert config["mode"] in {"timing", "lifetime"}
    assert config["operation"] in {"loads", "dumps"}
    assert config["scenario"] in {"plain", "prefetch_related"}
    assert config["validate"] in {"0", "1"}
    config["validate"] = config["validate"] == "1"
    assert config["rows"] > 0 and config["operations"] > 0 and config["repeat"] > 0
    assert config["warmups"] >= 0
    variant = os.getenv("NOVA_SERIAL_HINT_VARIANT")
    if variant is not None:
        assert variant in {"original", "without_instance"}
        assert config["operation"] == "loads" and config["scenario"] == "prefetch_related"
        config["hint_variant"] = variant
    return config


def require_clean_runtime():
    require_automatic_gc()
    assert not tracemalloc.is_tracing(), "Run without tracemalloc"
    assert sys.gettrace() is None and sys.getprofile() is None, "Run without tracing/profiling"
    coverage = sys.modules.get("coverage")
    assert coverage is None or coverage.Coverage.current() is None, "Run without coverage"
    assert connection.get_autocommit() and not connection.in_atomic_block
    assert not connection.queries_logged, "Disable DEBUG and the SQL debug cursor"


@contextmanager
def no_sql():
    """Fail on any SQL, including accidental lazy loads during validation."""

    def reject(execute, sql, params, many, context):
        raise AssertionError("SQL is forbidden after serializer preparation")

    with ExitStack() as stack:
        for alias in connections:
            stack.enter_context(connections[alias].execute_wrapper(reject))
        yield


def prepare(case, config, serializer):
    keys = seed_rows(case, config["rows"])
    query = make_query(case, keys, config["scenario"])
    rows = list(query)
    if "hint_variant" in config:
        control_query = make_query(case, keys, config["scenario"])
        control = list(control_query)
        with no_sql():
            payloads, proof = paired_payloads(rows, control, serializer)
            expected = fingerprint(rows, config["scenario"])
        payload = payloads[config["hint_variant"]]
        # Identical preparation in both arms; no graph is retained for timing.
        return {
            "value": payload,
            "reference_payload": payload,
            "expected": expected,
            "hint_control": proof,
        }
    expected = fingerprint(rows, config["scenario"])
    payload = serializer.dumps(rows)
    # Do not retain the temporary QuerySet or original models in a loads run.
    return {
        "value": payload if config["operation"] == "loads" else rows,
        "reference_payload": payload,
        "expected": expected,
    }


def validate_result(result, prepared, config):
    if config["operation"] == "loads":
        projected = fingerprint(result, config["scenario"])
        assert projected == prepared["expected"], "Deserialized models or relations changed"
        return projected
    # A round trip here would mix deserialization into the dumps experiment.
    assert result == prepared["reference_payload"], "Serialized bytes changed"
    return True


def summarize_gc(probe, operations):
    """Postprocess after observation; report every collection, including gaps."""
    windows = [(op["start_ns"], op["end_ns"]) for op in operations]
    validation = [
        (op["validation_start_ns"], op["validation_end_ns"])
        for op in operations
        if "validation_start_ns" in op
    ]
    events = [
        {
            "start_ns": start,
            "end_ns": end,
            "generation": gen,
            "thread_id": thread,
            "collected": collected,
            "uncollectable": uncollectable,
        }
        for start, end, gen, thread, collected, uncollectable in probe.collections
    ]
    generations = []
    for generation in range(3):
        selected = [event for event in events if event["generation"] == generation]
        operation_ns = validation_ns = outside_ns = outside_count = 0
        for event in selected:
            window = [(event["start_ns"], event["end_ns"])]
            overlap = interval_ns(intersect_intervals(window, windows))
            operation_ns += overlap
            validation_ns += interval_ns(intersect_intervals(window, validation))
            outside_ns += event["end_ns"] - event["start_ns"] - overlap
            outside_count += overlap == 0
        generations.append(
            {
                "generation": generation,
                "collections": len(selected),
                "collected": sum(e["collected"] for e in selected),
                "uncollectable": sum(e["uncollectable"] for e in selected),
                "duration_ns": sum(e["end_ns"] - e["start_ns"] for e in selected),
                "operation_overlap_ns": operation_ns,
                "validation_overlap_ns": validation_ns,
                "outside_operation_ns": outside_ns,
                "outside_operation_collections": outside_count,
            }
        )
    intervals = [(e["start_ns"], e["end_ns"]) for e in events]
    for op in operations:
        op["gc_overlap_ns"] = interval_ns(
            intersect_intervals([(op["start_ns"], op["end_ns"])], intervals)
        )
    return {
        "start_ns": probe.start_ns,
        "end_ns": probe.end_ns,
        "before": probe.before,
        "after": probe.after,
        "generations": generations,
        "events": events,
        "incomplete_gc_starts": len(probe.pending_gc),
        "unmatched_gc_stops": probe.unmatched_gc_stops,
    }


def run_timing(serializer, prepared, config):
    function = getattr(serializer, config["operation"])
    value = prepared["value"]
    with no_sql():
        with ArmDiagnostics(record_phases=False) as warmup:
            # Check correctness once even when per-operation validation is off.
            result = function(value)
            validate_result(result, prepared, config)
            del result
            for _ in range(config["warmups"]):
                result = function(value)
                if config["validate"]:
                    validate_result(result, prepared, config)
                del result
        operations = []
        with ArmDiagnostics(record_phases=False) as probe:
            for index in range(config["operations"]):
                start = time.perf_counter_ns()
                result = function(value)
                end = time.perf_counter_ns()
                assert result is not None, "Serializer failed to restore the payload"
                if config["validate"]:
                    validation_start = time.perf_counter_ns()
                    validate_result(result, prepared, config)
                    validation_end = time.perf_counter_ns()
                del result
                if "hint_variant" in config:
                    released_ns = time.perf_counter_ns()
                sample = {
                    "index": index,
                    "start_ns": start,
                    "end_ns": end,
                    "elapsed_ns": end - start,
                }
                if "hint_variant" in config:
                    sample["released_ns"] = released_ns
                if config["validate"]:
                    sample.update(
                        validation_start_ns=validation_start, validation_end_ns=validation_end
                    )
                operations.append(sample)
        # Report construction and intersections cannot affect later measured calls.
        gc_report = summarize_gc(probe, operations)
        warmup_report = summarize_gc(warmup, [])
        if config["operation"] == "dumps":
            assert fingerprint(value, config["scenario"]) == prepared["expected"]
    blocks = []
    for i in range(4):
        part = operations[i * len(operations) // 4 : (i + 1) * len(operations) // 4]
        blocks.append({"block": i + 1, **latency_summary(part)})
    return {
        "operations": operations,
        "gc": gc_report,
        "warmup_gc": warmup_report,
        "summary": {
            "latency": latency_summary(operations),
            "validation_latency": latency_summary(
                [
                    {"elapsed_ns": op["validation_end_ns"] - op["validation_start_ns"]}
                    for op in operations
                    if "validation_start_ns" in op
                ]
            ),
            "observed_loop_ms": (probe.end_ns - probe.start_ns) / 1_000_000,
            "blocks": blocks,
            "gc": gc_report["generations"],
            "sql_count": 0,
            "validation_calls": config["operations"] if config["validate"] else 0,
        },
    }


def run_lifetime(serializer, prepared, config):
    # Transfer input ownership so dropping it below really removes our strong ref.
    value = prepared.pop("value")
    input_graph, output_graph = WeakGraph(), WeakGraph()
    with no_sql():
        input_graph.capture(value if config["operation"] == "dumps" else [], None)
        baseline = collect_checkpoint(input_graph, "input_held_before_operation")
        result = getattr(serializer, config["operation"])(value)
        assert result is not None, "Serializer failed to restore the payload"
        output_graph.capture(result if config["operation"] == "loads" else [], None)
        held = output_graph.snapshot("result_held")
        projection = validate_result(result, prepared, config) if config["validate"] else None
        del result
        # Keep the primitive validation projection alive across this collection.
        released = collect_checkpoint(output_graph, "result_dropped_projection_held")
        del projection
        del value
        input_released = collect_checkpoint(input_graph, "input_dropped")
    return {
        "input_graph": input_graph.describe(),
        "output_graph": output_graph.describe(),
        "checkpoints": {
            "input_held": baseline,
            "result_held": held,
            "result_dropped": released,
            "input_dropped": input_released,
        },
        "summary": {
            "sql_count": 0,
            "input_observed": baseline["before"]["observed"],
            "result_observed": held["observed"],
            "result_alive_before_collection": released["before"]["alive"],
            "result_alive_after_collection": released["after"]["alive"],
            "input_alive_after_collection": input_released["after"]["alive"],
        },
    }


def test_serializer_experiment(relations):
    require_clean_runtime()
    config = configuration()
    output = Path(os.environ["NOVA_SERIAL_OUTPUT"]).expanduser().resolve()
    report = {
        "schema_version": 1,
        "experiment": "serializer_gc",
        "complete": False,
        "configuration": config,
        "process_id": os.getpid(),
        "started_utc": datetime.now(UTC).isoformat(),
        "environment": {
            **environment(),
            "gc_debug": gc.get_debug(),
            "pythonhashseed": os.getenv("PYTHONHASHSEED"),
        },
        "source_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (
                Path(__file__),
                Path(__file__).with_name("workload_diagnostics.py"),
                Path(__file__).with_name("object_lifetime.py"),
                Path(__file__).with_name("queryset_cache_cost.py"),
                Path(__file__).with_name("serializer_hint_graph.py"),
                Path(__file__).resolve().parents[1] / "scripts" / "run_serializer_gc.py",
            )
        },
        "method": {
            "input": "one materialized graph or one fixed payload per process",
            "serializer": "native PickleSerializer, no backend/client wrappers",
            "validation": "fingerprint of loaded models/relations"
            if config["operation"] == "loads"
            else "byte equality; never calls loads",
            "timing": "call only; validation, disposal and setup excluded from latency",
            "gc": "whole loop, including validation/disposal; counts are process-wide",
            "lifetime": "separate process; weakrefs and explicit collect(2); no call latency",
        },
    }
    try:
        serializer = PickleSerializer()
        with ArmDiagnostics(record_phases=False) as preparation:
            prepared = prepare(relations, config, serializer)
        report["payload_bytes"] = len(prepared["reference_payload"])
        report["payload_sha256"] = hashlib.sha256(prepared["reference_payload"]).hexdigest()
        if "hint_control" in prepared:
            report["hint_control"] = prepared["hint_control"]
        # Postpone preparation event expansion until the timed loop has finished.
        report.update(
            (run_timing if config["mode"] == "timing" else run_lifetime)(
                serializer, prepared, config
            )
        )
        report["preparation_gc"] = summarize_gc(preparation, [])
        report["complete"] = True
    finally:
        report["finished_utc"] = datetime.now(UTC).isoformat()
        write_report(report, output)
        print(f"Serializer report: {output}", flush=True)

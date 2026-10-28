"""Shared timing worker. Importing this module must never import pytest.

Run via scripts/run_serializer_environment.py. The minimal entry point performs
django.setup() and calls the same execute() used by the pytest adapter.
"""

import gc
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import time
import tracemalloc
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from benchmarks.workload_diagnostics import (
    ArmDiagnostics,
    intersect_intervals,
    interval_ns,
    latency_summary,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    "benchmarks/serializer_environment.py",
    "benchmarks/serializer_environment_settings.py",
    "benchmarks/serializer_environment_pytest.py",
    "benchmarks/serializer_environment_payloads.py",
    "benchmarks/serializer_gc.py",
    "benchmarks/queryset_cache_cost.py",
    "benchmarks/serializer_hint_graph.py",
    "benchmarks/workload_diagnostics.py",
    "scripts/run_serializer_environment.py",
    "src/nova/cache/backends/serializers.py",
    "tests/cache/relations_app/models.py",
    "tests/cache/relations_app/apps.py",
)


def policy():
    return {
        "enabled": gc.isenabled(),
        "thresholds": list(gc.get_threshold()),
        "debug": gc.get_debug(),
    }


def pytest_modules():
    return sorted(
        name
        for name in sys.modules
        if name == "pytest"
        or name.startswith(("pytest.", "pytest_", "_pytest"))
        or name.endswith(".pytest_plugin")
    )


def clean_runtime(worker, expected_policy):
    assert sys.flags.optimize == 0, "Run without Python optimization (-O)"
    assert gc.isenabled() and gc.get_threshold()[0] > 0
    assert not gc.get_debug() & gc.DEBUG_SAVEALL
    assert policy() == expected_policy, "GC policy differs from payload producer"
    assert not tracemalloc.is_tracing(), "Run without tracemalloc"
    assert sys.gettrace() is None and sys.getprofile() is None, "Run without tracing/profiling"
    coverage = sys.modules.get("coverage")
    assert coverage is None or coverage.Coverage.current() is None, "Run without coverage"
    if worker == "minimal":
        assert not pytest_modules(), "Minimal worker imported pytest or a pytest plugin"
    else:
        assert worker == "pytest" and "pytest" in sys.modules


@contextmanager
def no_sql():
    from django.db import connections

    def reject(execute, sql, params, many, context):
        raise AssertionError("SQL is forbidden in serializer environment workers")

    with ExitStack() as stack:
        for alias in connections:
            connection = connections[alias]
            assert not connection.queries_logged, "Disable DEBUG and SQL debug cursor"
            stack.enter_context(connection.execute_wrapper(reject))
        yield


def fingerprint(rows, scenario="prefetch_related"):
    # Keep the previous validator's allocations and return shape unchanged.
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


def scope_report(probe, operations=()):
    events = [
        {
            "start_ns": a,
            "end_ns": b,
            "generation": g,
            "thread_id": t,
            "collected": c,
            "uncollectable": u,
        }
        for a, b, g, t, c, u in probe.collections
    ]
    intervals = [(e["start_ns"], e["end_ns"]) for e in events]
    for operation in operations:
        operation["gc_overlap_ns"] = interval_ns(
            intersect_intervals([(operation["start_ns"], operation["end_ns"])], intervals)
        )
    return {
        "start_ns": probe.start_ns,
        "end_ns": probe.end_ns,
        "before": probe.before,
        "after": probe.after,
        "events": events,
        "incomplete_gc_starts": len(probe.pending_gc),
        "unmatched_gc_stops": probe.unmatched_gc_stops,
    }


def timed_reads(serializer, payload, expected, config):
    # Same loop and boundary placement in both environments. No backend wrappers,
    # explicit collections, weakrefs, SQL counters with per-query intervals, or
    # process CPU timestamps are added to the measured path.
    function = serializer.loads
    with no_sql():
        with ArmDiagnostics(record_phases=False) as warmup:
            result = function(payload)
            assert fingerprint(result) == expected
            del result
            for _ in range(config["warmups"]):
                result = function(payload)
                if config["validate"]:
                    assert fingerprint(result) == expected
                del result
        operations = []
        with ArmDiagnostics(record_phases=False) as probe:
            for index in range(config["operations"]):
                start = time.perf_counter_ns()
                result = function(payload)
                end = time.perf_counter_ns()
                assert result is not None, "Serializer failed to restore the payload"
                if config["validate"]:
                    validation_start = time.perf_counter_ns()
                    assert fingerprint(result) == expected
                    validation_end = time.perf_counter_ns()
                del result
                released = time.perf_counter_ns()
                sample = {
                    "index": index,
                    "start_ns": start,
                    "end_ns": end,
                    "elapsed_ns": end - start,
                    "released_ns": released,
                }
                if config["validate"]:
                    sample.update(
                        validation_start_ns=validation_start, validation_end_ns=validation_end
                    )
                operations.append(sample)
    # Build summaries only after the last measured operation.
    return {
        "operations": operations,
        "gc": scope_report(probe, operations),
        "warmup_gc": scope_report(warmup),
        "summary": {
            "latency": latency_summary(operations),
            "sql_count": 0,
            "observed_loop_ms": (probe.end_ns - probe.start_ns) / 1e6,
            "validation_calls": config["operations"] if config["validate"] else 0,
        },
    }


def metadata():
    from django import get_version
    from django.conf import settings

    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "django": get_version(),
        "platform": platform.platform(),
        "executable": sys.executable,
        "versions": sorted(
            (d.metadata["Name"], d.version) for d in importlib.metadata.distributions()
        ),
        "gc_policy": policy(),
        "pythonhashseed": os.getenv("PYTHONHASHSEED"),
        "base_settings": os.environ["NOVA_ENV_BASE_SETTINGS"],
        "installed_apps": list(settings.INSTALLED_APPS),
        "database_engine": settings.DATABASES["default"]["ENGINE"],
        "clock_resolution_seconds": time.get_clock_info("perf_counter").resolution,
    }


def load_input(directory, config, serializer):
    from benchmarks.serializer_hint_graph import loaded_values

    directory = Path(directory)
    raw = (directory / "payloads.json").read_bytes()
    bundle = json.loads(raw)
    assert bundle["complete"] and bundle["experiment"] == "serializer_environment_payloads"
    assert bundle["schema_version"] == 1
    assert bundle["rows"] == config["rows"]
    item = bundle["payloads"][config["hint_variant"]]
    path = (directory / item["file"]).resolve()
    assert path.is_relative_to(directory.resolve()), "Payload path escapes bundle"
    payload = path.read_bytes()
    assert (
        len(payload) == item["bytes"] and hashlib.sha256(payload).hexdigest() == item["sha256"]
    ), "Payload checksum mismatch"
    expected = [(pk, title, tuple(tags)) for pk, title, tags in bundle["expected"]]
    restored = serializer.loads(payload)
    assert fingerprint(restored) == expected
    semantic = json.dumps(loaded_values(restored), sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(semantic).hexdigest() == bundle["hint_control"]["semantic_sha256"]
    for row in restored:
        hints = row._prefetched_objects_cache["tags"]._hints
        if config["hint_variant"] == "original":
            assert hints.get("instance") is row
        else:
            assert "instance" not in hints
    return payload, expected, bundle, hashlib.sha256(raw).hexdigest()


def execute(worker, plugins=()):
    from nova.cache.backends.serializers import PickleSerializer

    config = json.loads(Path(os.environ["NOVA_ENV_JOB"]).read_text())
    assert config["worker"] == worker
    output = Path(os.environ["NOVA_ENV_OUTPUT"])
    assert not output.exists(), "Worker report already exists"
    bundle = json.loads((Path(os.environ["NOVA_ENV_PAYLOAD_DIR"]) / "payloads.json").read_text())
    report = {
        "schema_version": 1,
        "experiment": "serializer_environment_worker",
        "complete": False,
        "configuration": config,
        "process_id": os.getpid(),
        "started_utc": datetime.now(UTC).isoformat(),
        "source_sha256": {
            p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCE_FILES
        },
        "plugins": list(plugins),
    }
    try:
        clean_runtime(worker, bundle["gc_policy"])
        report["environment"] = metadata()
        report["runtime_before"] = {
            "pytest_modules": pytest_modules(),
            "module_count": len(sys.modules),
        }
        serializer = PickleSerializer()
        with no_sql(), ArmDiagnostics(record_phases=False) as preparation:
            payload, expected, bundle, digest = load_input(
                os.environ["NOVA_ENV_PAYLOAD_DIR"], config, serializer
            )
        report["payload_bundle_sha256"] = digest
        report["payload_sha256"] = hashlib.sha256(payload).hexdigest()
        report["hint_control"] = bundle["hint_control"]
        report.update(timed_reads(serializer, payload, expected, config))
        report["preparation_gc"] = scope_report(preparation)
        clean_runtime(worker, bundle["gc_policy"])
        report["runtime_after"] = {
            "pytest_modules": pytest_modules(),
            "module_count": len(sys.modules),
        }
        report["complete"] = True
    finally:
        report["finished_utc"] = datetime.now(UTC).isoformat()
        with output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    return report


def main():
    import django

    os.environ["DJANGO_SETTINGS_MODULE"] = "benchmarks.serializer_environment_settings"
    django.setup()
    execute("minimal")


if __name__ == "__main__":
    main()

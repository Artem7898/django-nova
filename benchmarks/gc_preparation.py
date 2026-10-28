"""Compare automatic GC after identical warmup, with/without one full collection."""

import gc
import hashlib
import json
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from benchmarks import pytest_plugin_profile as profiles
from benchmarks import serializer_environment as common
from benchmarks.serializer_environment import fingerprint, no_sql, scope_report
from benchmarks.workload_diagnostics import ArmDiagnostics, latency_summary

SOURCE_FILES = (
    *profiles.SOURCE_FILES,
    "benchmarks/gc_preparation.py",
    "benchmarks/gc_preparation_test.py",
    "scripts/run_gc_preparation.py",
    "scripts/summarize_gc_preparation.py",
)


def timed_reads(serializer, payload, expected, config):
    """Preserve the old warmup/read loops; condition GC only between them."""
    function = serializer.loads
    preparation_call = [None, None, None]
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
        with ArmDiagnostics(record_phases=False) as conditioning:
            preparation_call[0] = time.perf_counter_ns()
            if config["prepare_gc"]:
                preparation_call[2] = gc.collect(2)
            preparation_call[1] = time.perf_counter_ns()
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
        "conditioning_gc": scope_report(conditioning),
        "conditioning_call": {
            "requested": config["prepare_gc"],
            "start_ns": preparation_call[0],
            "end_ns": preparation_call[1],
            "returned": preparation_call[2],
        },
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


def execute(pytest_config):
    from nova.cache.backends.serializers import PickleSerializer

    job = json.loads(Path(os.environ["NOVA_GC_PREP_JOB"]).read_text())
    assert job["worker"] == "pytest" and job["mode"] == "timing"
    assert job["hint_variant"] == "without_instance" and type(job["prepare_gc"]) is bool
    output = Path(os.environ["NOVA_GC_PREP_OUTPUT"])
    assert not output.exists(), "Report already exists"
    directory = Path(os.environ["NOVA_ENV_PAYLOAD_DIR"])
    raw_bundle = (directory / "payloads.json").read_bytes()
    bundle = json.loads(raw_bundle)
    assert "benchmarks.gc_inventory" not in sys.modules
    report = {
        "schema_version": 1,
        "experiment": "gc_preparation_worker",
        "complete": False,
        "configuration": job,
        "process_id": os.getpid(),
        "started_utc": datetime.now(UTC).isoformat(),
        "source_sha256": {
            path: hashlib.sha256((common.ROOT / path).read_bytes()).hexdigest()
            for path in SOURCE_FILES
        },
        "reference_sha256": os.environ["NOVA_GC_PREP_REFERENCE_SHA256"],
        "payload_bundle_sha256": hashlib.sha256(raw_bundle).hexdigest(),
    }
    try:
        before = profiles.plugin_evidence(pytest_config)
        report["plugins"] = before["distributions"]
        report["profile_evidence"] = {"before": before}
        profiles.check_plugins(before, job)
        common.clean_runtime("pytest", bundle["gc_policy"])
        report["environment"] = common.metadata()
        report["runtime_before"] = {
            "pytest_modules": common.pytest_modules(),
            "module_count": len(sys.modules),
        }
        assert len(threading.enumerate()) == 1 and not gc.callbacks
        report["gc_guard_before"] = {
            "policy": common.policy(),
            "frozen": gc.get_freeze_count(),
            "callbacks": len(gc.callbacks),
            "python_threads": len(threading.enumerate()),
        }
        serializer = PickleSerializer()
        with common.no_sql(), ArmDiagnostics(record_phases=False) as preparation:
            payload, expected, bundle, digest = common.load_input(directory, job, serializer)
        assert digest == report["payload_bundle_sha256"]
        report["payload_sha256"] = hashlib.sha256(payload).hexdigest()
        report["hint_control"] = bundle["hint_control"]
        report.update(timed_reads(serializer, payload, expected, job))
        report["preparation_gc"] = common.scope_report(preparation)
        report["gc_guard_after"] = {
            "policy": common.policy(),
            "frozen": gc.get_freeze_count(),
            "callbacks": len(gc.callbacks),
            "python_threads": len(threading.enumerate()),
        }
        assert report["gc_guard_after"] == report["gc_guard_before"]
        assert "benchmarks.gc_inventory" not in sys.modules
        common.clean_runtime("pytest", bundle["gc_policy"])
        after = profiles.plugin_evidence(pytest_config)
        profiles.check_plugins(after, job)
        assert after == before, "Plugin registration/configuration changed during the worker"
        report["profile_evidence"]["after"] = after
        report["runtime_after"] = {
            "pytest_modules": common.pytest_modules(),
            "module_count": len(sys.modules),
            "census_imported": "benchmarks.gc_inventory" in sys.modules,
        }
        report["complete"] = True
    finally:
        report["finished_utc"] = datetime.now(UTC).isoformat()
        with output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    return report

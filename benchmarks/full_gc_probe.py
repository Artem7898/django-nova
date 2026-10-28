"""One explicitly requested full-GC measurement in a disposable pytest worker."""

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
from benchmarks.workload_diagnostics import ArmDiagnostics

SOURCE_FILES = (
    *profiles.SOURCE_FILES,
    "benchmarks/full_gc_probe.py",
    "benchmarks/full_gc_probe_test.py",
    "scripts/run_full_gc_probe.py",
    "scripts/summarize_full_gc_probe.py",
)


def capture_state(target):
    """Copy into preallocated primitive slots; drop temporary stats before returning."""
    target[0] = gc.isenabled()
    threshold = gc.get_threshold()
    target[1], target[2], target[3] = threshold
    target[4] = gc.get_debug()
    target[5] = gc.get_freeze_count()
    counts = gc.get_count()
    target[6], target[7], target[8] = counts
    stats = gc.get_stats()
    for generation in range(3):
        offset = 9 + generation * 3
        item = stats[generation]
        target[offset] = item["collections"]
        target[offset + 1] = item["collected"]
        target[offset + 2] = item["uncollectable"]
    target[18] = len(gc.callbacks)


def state_report(values):
    return {
        "policy": {"enabled": values[0], "thresholds": values[1:4], "debug": values[4]},
        "frozen_objects": values[5],
        "counts": values[6:9],
        "stats": [
            dict(zip(("collections", "collected", "uncollectable"), values[i : i + 3], strict=True))
            for i in (9, 12, 15)
        ],
        "callback_count": values[18],
    }


def collect_pair():
    """Prepare once, measure once; no serializer call or live result in this frame."""
    if len(threading.enumerate()) != 1:
        raise AssertionError("Full-GC probe requires one Python thread")
    if not gc.isenabled() or gc.callbacks:
        raise AssertionError("Require enabled GC and no external GC callbacks")
    # All persistent observation buffers exist before the preparatory collection.
    states = [[0] * 19 for _ in range(5)]
    before, preparation, measured, after, restored = states
    collect, clock = gc.collect, time.perf_counter_ns
    capture_state(before)
    gc.disable()
    try:
        capture_state(preparation)
        preparation_return = collect(2)
        capture_state(measured)
        start = clock()
        measured_return = collect(2)
        end = clock()
        capture_state(after)
    finally:
        gc.enable()
    capture_state(restored)
    assert common.policy() == state_report(before)["policy"]
    return {
        "preparation_return": preparation_return,
        "measured_return": measured_return,
        "start_ns": start,
        "end_ns": end,
        "elapsed_ns": end - start,
        "states": dict(
            zip(
                ("before", "before_preparation", "before_measured", "after_measured", "restored"),
                (state_report(s) for s in states),
                strict=True,
            )
        ),
        "automatic_gc_suspended_during_pair": True,
        "explicit_collections": 2,
        "measured_collections": 1,
        "python_threads": 1,
    }


def warmup(serializer, payload, expected, config):
    """Same preflight/warmup disposal schedule, without importing the census module."""
    function = serializer.loads
    with common.no_sql(), ArmDiagnostics(record_phases=False) as observer:
        result = function(payload)
        assert common.fingerprint(result) == expected
        del result
        for _ in range(config["warmups"]):
            result = function(payload)
            if config["validate"]:
                assert common.fingerprint(result) == expected
            del result
    return observer


def execute(pytest_config):
    from nova.cache.backends.serializers import PickleSerializer

    job = json.loads(Path(os.environ["NOVA_FULL_GC_JOB"]).read_text())
    assert job["worker"] == "pytest" and job["hint_variant"] == "without_instance"
    assert job["operation"] == "full_gc_probe" and job["scenario"] == "prefetch_related"
    output = Path(os.environ["NOVA_FULL_GC_OUTPUT"])
    assert not output.exists(), "Worker report already exists"
    directory = Path(os.environ["NOVA_ENV_PAYLOAD_DIR"])
    raw = (directory / "payloads.json").read_bytes()
    bundle = json.loads(raw)
    report = {
        "schema_version": 1,
        "experiment": "full_gc_probe_worker",
        "complete": False,
        "configuration": job,
        "process_id": os.getpid(),
        "started_utc": datetime.now(UTC).isoformat(),
        "source_sha256": {
            p: hashlib.sha256((common.ROOT / p).read_bytes()).hexdigest() for p in SOURCE_FILES
        },
        "reference_sha256": os.environ["NOVA_FULL_GC_REFERENCE_SHA256"],
        "payload_bundle_sha256": hashlib.sha256(raw).hexdigest(),
    }
    try:
        before = profiles.plugin_evidence(pytest_config)
        report["profile_evidence"] = {"before": before}
        report["plugins"] = before["distributions"]
        profiles.check_plugins(before, job)
        common.clean_runtime("pytest", bundle["gc_policy"])
        report["environment"] = common.metadata()
        assert "benchmarks.gc_inventory" not in sys.modules
        serializer = PickleSerializer()
        with common.no_sql(), ArmDiagnostics(record_phases=False) as preparation:
            payload, expected, bundle, digest = common.load_input(directory, job, serializer)
        assert digest == report["payload_bundle_sha256"]
        report["payload_sha256"] = hashlib.sha256(payload).hexdigest()
        report["hint_control"] = bundle["hint_control"]
        warmed = warmup(serializer, payload, expected, job)
        # Both functions holding restored ORM objects have returned. The retained
        # payload is bytes; expected values and observers contain no ORM instances.
        with common.no_sql():
            report["probe"] = collect_pair()
        report["prefix_gc"] = {
            "preparation": common.scope_report(preparation),
            "warmup": common.scope_report(warmed),
        }
        report["work"] = {
            "preflight_loads": 1,
            "warmup_loads": 1 + job["warmups"],
            "warmup_validation_calls": 1 + (job["warmups"] if job["validate"] else 0),
            "timed_loads": 0,
            "sql_count": 0,
            "snapshots": 0,
        }
        common.clean_runtime("pytest", bundle["gc_policy"])
        after = profiles.plugin_evidence(pytest_config)
        profiles.check_plugins(after, job)
        report["profile_evidence"]["after"] = after
        assert before == after, "Plugin registration changed during worker"
        assert "benchmarks.gc_inventory" not in sys.modules
        report["runtime_after"] = {"census_imported": False}
        report["complete"] = True
    finally:
        report["finished_utc"] = datetime.now(UTC).isoformat()
        with output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    return report

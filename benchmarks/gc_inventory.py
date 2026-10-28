"""Terminal GC inventories in disposable workers, never in latency workers."""

import gc
import hashlib
import json
import os
import sys
import threading
import types
from datetime import UTC, datetime
from pathlib import Path

from benchmarks import serializer_environment as common
from benchmarks.workload_diagnostics import ArmDiagnostics

SOURCE_FILES = (
    *common.SOURCE_FILES,
    "benchmarks/gc_inventory.py",
    "benchmarks/gc_inventory_pytest.py",
    "scripts/run_gc_inventory.py",
    "scripts/summarize_gc_inventory.py",
)


def plain_name(value):
    return value if type(value) is str else "<unknown>"


def type_label(cls):
    # Bypass instance and custom metaclass __getattribute__. Never repr(),
    # sizeof(), stringify, or inspect descriptors on sampled instances.
    return (
        plain_name(type.__getattribute__(cls, "__module__")),
        plain_name(type.__getattribute__(cls, "__qualname__")),
    )


def increment(table, key, generation):
    counts = table.get(key)
    if counts is None:
        counts = table[key] = [0, 0, 0]
    counts[generation] += 1


def rows(table):
    return [
        {"label": label, "generations": counts, "total": sum(counts)}
        for label, counts in sorted(table.items())
    ]


def count_objects(generations, excluded):
    """Return only primitive summaries; retain no sampled object in the result."""
    by_type, functions, classes, modules = {}, {}, {}, {}
    labels, seen = {}, set()
    counts, omitted = [0, 0, 0], [0, 0, 0]
    for generation, objects in enumerate(generations):
        for obj in objects:
            identity = id(obj)
            if identity in excluded:
                omitted[generation] += 1
                continue
            if identity in seen:
                raise AssertionError("Object appears in multiple inventory positions")
            seen.add(identity)
            cls = type(obj)
            cached = labels.get(id(cls))
            if cached is None:
                module, name = type_label(cls)
                cached = labels[id(cls)] = (
                    f"{module}.{name}",
                    type in type.__getattribute__(cls, "__mro__"),
                )
            increment(by_type, cached[0], generation)
            counts[generation] += 1
            if cls is types.FunctionType:
                increment(
                    functions, plain_name(object.__getattribute__(obj, "__module__")), generation
                )
            elif cls is types.ModuleType:
                increment(modules, plain_name(object.__getattribute__(obj, "__name__")), generation)
            elif cached[1]:
                increment(classes, plain_name(type.__getattribute__(obj, "__module__")), generation)
    return {
        "generation_counts": counts,
        "total": sum(counts),
        "excluded_observer_counts": omitted,
        "types": rows(by_type),
        "functions_by_declared_module": rows(functions),
        "classes_by_declared_module": rows(classes),
        "module_objects_by_name": rows(modules),
    }


def take_inventory():
    """One terminal snapshot, without a forced collection or policy retuning.

    Automatic GC is suspended only after reaching the target checkpoint, so
    inspection allocations cannot promote/collect objects while enumerating.
    The temporary strong-reference lists are dropped before restoring GC.
    """
    if len(threading.enumerate()) != 1:
        raise AssertionError("Inventory requires one Python thread")
    frozen = gc.get_freeze_count()
    enabled = gc.isenabled()
    if not enabled:
        raise AssertionError("Automatic GC must be enabled before the checkpoint")
    gc.disable()
    oldest = middle = youngest = inventories = None
    try:
        policy = {
            "enabled": enabled,
            "thresholds": list(gc.get_threshold()),
            "debug": gc.get_debug(),
        }
        before = gc.get_stats()
        # Inspector-owned result lists enter gen-0. Enumerating it last makes
        # their exclusion explicit, and leaves generations 1/2 uncontaminated
        # by allocations performed after the suspension boundary.
        oldest = gc.get_objects(2)
        middle = gc.get_objects(1)
        youngest = gc.get_objects(0)
        after_acquisition = gc.get_stats()
        excluded = {
            id(oldest),
            id(middle),
            id(policy),
            id(policy["thresholds"]),
            id(before),
            *(id(item) for item in before),
        }
        inventories = (youngest, middle, oldest)
        summary = count_objects(inventories, excluded)
        after = gc.get_stats()
        assert before == after_acquisition == after, "GC ran during suspended inspection"
        assert (
            list(gc.get_threshold()) == policy["thresholds"] and gc.get_debug() == policy["debug"]
        )
        summary["inspection"] = {
            "automatic_gc_suspended": True,
            "forced_collections": 0,
            "policy_before": policy,
            "stats_before": before,
            "stats_after_acquisition": after_acquisition,
            "stats_after_aggregation": after,
            "acquisition_order": [2, 1, 0],
            "python_threads": 1,
            "frozen_objects_before": frozen,
            "frozen_objects_after": gc.get_freeze_count(),
            "raw_generation_counts": [len(youngest), len(middle), len(oldest)],
        }
    finally:
        # Keep no inventory list alive when automatic collection resumes.
        oldest = middle = youngest = inventories = None
        gc.enable()
    summary["inspection"]["policy_restored"] = common.policy()
    assert summary["inspection"]["policy_restored"] == policy
    return summary


def warmup(serializer, payload, expected, config):
    """Same load/validate/delete schedule as the timing worker; no timed loop."""
    function = serializer.loads
    with common.no_sql(), ArmDiagnostics(record_phases=False) as probe:
        result = function(payload)
        assert common.fingerprint(result) == expected
        del result
        for _ in range(config["warmups"]):
            result = function(payload)
            if config["validate"]:
                assert common.fingerprint(result) == expected
            del result
    return probe


def execute(worker, plugins=()):
    from nova.cache.backends.serializers import PickleSerializer

    config = json.loads(Path(os.environ["NOVA_GC_JOB"]).read_text())
    assert config["worker"] == worker
    assert config["checkpoint"] in ("startup", "after_warmup")
    output = Path(os.environ["NOVA_GC_OUTPUT"])
    assert not output.exists(), "Inventory report already exists"
    directory = Path(os.environ["NOVA_ENV_PAYLOAD_DIR"])
    raw_bundle = (directory / "payloads.json").read_bytes()
    bundle = json.loads(raw_bundle)
    report = {
        "schema_version": 1,
        "experiment": "gc_inventory_worker",
        "complete": False,
        "configuration": config,
        "process_id": os.getpid(),
        "started_utc": datetime.now(UTC).isoformat(),
        "plugins": list(plugins),
        "source_sha256": {
            p: hashlib.sha256((common.ROOT / p).read_bytes()).hexdigest() for p in SOURCE_FILES
        },
        "reference_sha256": os.environ["NOVA_GC_REFERENCE_SHA256"],
        "payload_bundle_sha256": hashlib.sha256(raw_bundle).hexdigest(),
    }
    try:
        common.clean_runtime(worker, bundle["gc_policy"])
        report["environment"] = common.metadata()
        report["runtime_before"] = {
            "pytest_modules": common.pytest_modules(),
            "module_count": len(sys.modules),
        }
        serializer = PickleSerializer()
        preparation = warmed = None
        if config["checkpoint"] == "after_warmup":
            with common.no_sql(), ArmDiagnostics(record_phases=False) as preparation:
                payload, expected, bundle, digest = common.load_input(directory, config, serializer)
            assert digest == report["payload_bundle_sha256"]
            report["payload_sha256"] = hashlib.sha256(payload).hexdigest()
            warmed = warmup(serializer, payload, expected, config)
        else:
            assert config["hint_variant"] is None and config["validate"] is None
        # Exactly one snapshot. No census is taken earlier in after_warmup
        # workers, and no latency measurement follows it.
        with common.no_sql():
            report["inventory"] = take_inventory()
        report["prefix_gc"] = {
            name: common.scope_report(probe)
            for name, probe in (("preparation", preparation), ("warmup", warmed))
            if probe is not None
        }
        active = config["checkpoint"] == "after_warmup"
        report["work"] = {
            "preflight_loads": int(active),
            "warmup_loads": 1 + config["warmups"] if active else 0,
            "warmup_validation_calls": 1 + (config["warmups"] if config["validate"] else 0)
            if active
            else 0,
            "timed_operations": 0,
            "sql_count": 0,
            "snapshots": 1,
        }
        common.clean_runtime(worker, bundle["gc_policy"])
        report["runtime_after"] = {
            "pytest_modules": common.pytest_modules(),
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

"""Isolation, ownership and reporting contracts, never latency assertions."""

import gc
import json
import weakref
from copy import deepcopy
from types import SimpleNamespace

import pytest
from benchmarks import serializer_gc as experiment
from benchmarks.workload_diagnostics import ArmDiagnostics
from django.db import connection
from scripts import run_serializer_gc as runner

from nova.cache.backends.serializers import PickleSerializer
from tests.cache.test_related_invalidation import relations as relations


def config(operation="loads", scenario="prefetch_related", validate=False, mode="timing"):
    return {
        "mode": mode,
        "operation": operation,
        "scenario": scenario,
        "validate": validate,
        "rows": 2,
        "operations": 8,
        "warmups": 1,
        "repeat": 1,
    }


def forbidden(*args, **kwargs):
    pytest.fail(
        "Timing attempted GC control, weak observation, phase probing or the other serializer"
    )


@pytest.mark.parametrize("operation", ["loads", "dumps"])
@pytest.mark.parametrize("scenario", ["plain", "prefetch_related"])
@pytest.mark.parametrize("validate", [False, True])
def test_timing_calls_only_one_serializer_without_sql_or_gc_control(
    relations, monkeypatch, operation, scenario, validate
):
    cfg = config(operation, scenario, validate)
    serializer = PickleSerializer()
    prepared = experiment.prepare(relations, cfg, serializer)
    value = prepared["value"]
    original = getattr(serializer, operation)
    seen, references = [], []

    def observe(argument):
        assert argument is value
        result = original(argument)
        seen.append(type(result))
        if operation == "loads":
            references.extend(weakref.ref(row) for row in result)
        return result

    monkeypatch.setattr(serializer, operation, observe)
    monkeypatch.setattr(serializer, "dumps" if operation == "loads" else "loads", forbidden)
    before = (gc.isenabled(), gc.get_threshold(), gc.get_debug(), list(gc.callbacks))
    with monkeypatch.context() as guards:
        for name in ("collect", "set_threshold", "enable", "disable", "freeze", "unfreeze"):
            guards.setattr(gc, name, forbidden)
        guards.setattr(experiment, "WeakGraph", forbidden)
        guards.setattr(experiment, "collect_checkpoint", forbidden)
        for name in ("instrument_cache", "wrap", "call"):
            guards.setattr(ArmDiagnostics, name, forbidden)
        report = experiment.run_timing(serializer, prepared, cfg)
    assert (gc.isenabled(), gc.get_threshold(), gc.get_debug(), list(gc.callbacks)) == before
    assert len(seen) == 1 + cfg["warmups"] + cfg["operations"]
    assert report["summary"]["sql_count"] == 0
    assert report["summary"]["latency"]["count"] == cfg["operations"]
    assert report["summary"]["validation_calls"] == (8 if validate else 0)
    assert report["summary"]["validation_latency"]["count"] == (8 if validate else 0)
    assert (
        report["summary"]["observed_loop_ms"]
        >= sum(op["elapsed_ns"] for op in report["operations"]) / 1_000_000
    )
    previous_end = 0
    for op in report["operations"]:
        assert op["start_ns"] >= previous_end
        assert op["elapsed_ns"] == op["end_ns"] - op["start_ns"]
        assert 0 <= op["gc_overlap_ns"] <= op["elapsed_ns"]
        assert ("validation_start_ns" in op) == validate
        previous_end = op.get("validation_end_ns", op["end_ns"])
        if validate:
            assert op["end_ns"] <= op["validation_start_ns"] <= previous_end
    json.dumps(report, allow_nan=False)
    # Explicit collection belongs to this ownership test, after timing stopped.
    gc.collect(2)
    assert all(reference() is None for reference in references)


@pytest.mark.parametrize("operation", ["loads", "dumps"])
@pytest.mark.parametrize("scenario", ["plain", "prefetch_related"])
@pytest.mark.parametrize("validate", [False, True])
def test_lifetime_releases_graphs_without_recording_call_latency(
    relations, operation, scenario, validate
):
    cfg = config(operation, scenario, validate, "lifetime")
    serializer = PickleSerializer()
    prepared = experiment.prepare(relations, cfg, serializer)
    before = (gc.isenabled(), gc.get_threshold(), list(gc.callbacks))
    report = experiment.run_lifetime(serializer, prepared, cfg)
    assert "value" not in prepared
    assert "operations" not in report and "latency" not in report["summary"]
    assert report["summary"]["result_alive_after_collection"] == 0
    assert report["summary"]["input_alive_after_collection"] == 0
    graph = report["output_graph" if operation == "loads" else "input_graph"]
    assert any(n["kind"].endswith(".article") for n in graph["nodes"])
    if scenario == "prefetch_related":
        assert any(e["relation"] == "prefetch:tags" for e in graph["edges"])
        assert any(e["relation"] == "hint:instance" for e in graph["edges"])
    assert (gc.isenabled(), gc.get_threshold(), list(gc.callbacks)) == before
    json.dumps(report, allow_nan=False)


def test_timing_cleans_up_gc_callback_on_serializer_failure(relations):
    cfg, serializer = config(), PickleSerializer()
    prepared = experiment.prepare(relations, cfg, serializer)
    original = serializer.loads
    calls = 0

    def fail(value):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise ValueError("controlled serializer failure")
        return original(value)

    serializer.loads = fail
    callbacks = list(gc.callbacks)
    with pytest.raises(ValueError, match="controlled"):
        experiment.run_timing(serializer, prepared, cfg)
    assert gc.callbacks == callbacks


def test_validation_detects_corruption_without_lazy_sql(relations):
    serializer, cfg = PickleSerializer(), config()
    prepared = experiment.prepare(relations, cfg, serializer)
    rows = serializer.loads(prepared["value"])
    rows[0].tags.all()[0].name = "corrupted"
    with experiment.no_sql(), pytest.raises(AssertionError, match="relations changed"):
        experiment.validate_result(rows, prepared, cfg)
    cfg = config("dumps")
    with pytest.raises(AssertionError, match="bytes changed"):
        experiment.validate_result(b"invalid", prepared, cfg)


def test_sql_guard_rejects_queries(relations):
    with (
        experiment.no_sql(),
        pytest.raises(AssertionError, match="SQL is forbidden"),
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT 1")


def test_gc_summary_separates_operation_validation_and_disposal():
    with ArmDiagnostics(record_phases=False) as probe:
        pass
    probe.collections = [(12, 18, 0, 1, 3, 0), (22, 28, 1, 1, 4, 0), (35, 40, 2, 1, 7, 0)]
    operations = [
        {
            "start_ns": 10,
            "end_ns": 20,
            "validation_start_ns": 21,
            "validation_end_ns": 30,
            "elapsed_ns": 10,
        }
    ]
    result = experiment.summarize_gc(probe, operations)
    a, b, c = result["generations"]
    assert (a["operation_overlap_ns"], a["outside_operation_ns"]) == (6, 0)
    assert (b["validation_overlap_ns"], b["outside_operation_ns"]) == (6, 6)
    assert (c["operation_overlap_ns"], c["outside_operation_ns"]) == (0, 5)
    assert operations[0]["gc_overlap_ns"] == 6


def test_runner_matrix_and_child_environment_are_explicit(monkeypatch, tmp_path):
    args = runner.arguments(["--output-dir", str(tmp_path / "new")])
    jobs = runner.jobs_for(args)
    assert len(jobs) == 64 and jobs == runner.jobs_for(args)
    assert len({tuple(sorted(job.items())) for job in jobs}) == 64
    monkeypatch.setenv("NOVA_SERIAL_OPERATION", "wrong")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--cov=src/nova")
    monkeypatch.setenv("COVERAGE_PROCESS_START", "old")
    monkeypatch.setenv("COV_CORE_SOURCE", "old")
    monkeypatch.setenv("PYTHONTRACEMALLOC", "1")
    env = runner.child_environment(jobs[0], tmp_path / "out.json")
    assert env["PYTEST_ADDOPTS"] == "" and env["PYTHONHASHSEED"] == "0"
    assert env["NOVA_SERIAL_OPERATION"] == jobs[0]["operation"]
    assert not any(k.startswith(("COVERAGE_", "COV_CORE_")) for k in env)
    assert "PYTHONTRACEMALLOC" not in env


def test_runner_stops_on_failed_child_and_keeps_incomplete_manifest(monkeypatch, tmp_path):
    output = tmp_path / "results"
    args = runner.arguments(["--output-dir", str(output)])
    calls = []

    def failure(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=2)

    monkeypatch.setattr(runner.subprocess, "run", failure)
    with pytest.raises(RuntimeError, match="Worker failed"):
        runner.run(args)
    report = json.loads((output / "manifest.json").read_text())
    assert report["complete"] is False
    assert len(calls) == len(report["runs"]) == 1
    assert report["runs"][0]["returncode"] == 2
    with pytest.raises(FileExistsError):
        runner.run(args)


@pytest.mark.parametrize("fault", ["incomplete", "configuration", "source", "callbacks", "gc"])
def test_runner_rejects_unusable_or_incomparable_reports(fault):
    cfg = config()
    state = {"enabled": True, "thresholds": [700, 10, 10]}
    scope = {"before": state, "after": state, "incomplete_gc_starts": 0, "unmatched_gc_stops": 0}
    report = {
        "complete": True,
        "experiment": "serializer_gc",
        "configuration": cfg.copy(),
        "process_id": runner.os.getpid() + 1,
        "environment": {"gc_thresholds": [700, 10, 10]},
        "source_sha256": {"experiment.py": "same-source"},
        "preparation_gc": deepcopy(scope),
        "warmup_gc": deepcopy(scope),
        "gc": deepcopy(scope),
    }
    reference = deepcopy(runner.check_report(report, cfg, None))
    if fault == "incomplete":
        report["complete"] = False
    elif fault == "configuration":
        report["configuration"]["validate"] = True
    elif fault == "source":
        report["source_sha256"]["experiment.py"] = "changed-source"
    elif fault == "callbacks":
        report["gc"]["unmatched_gc_stops"] = 1
    else:
        report["gc"]["after"]["enabled"] = False
    with pytest.raises(ValueError):
        runner.check_report(report, cfg, reference)


def test_clean_runtime_rejects_active_coverage(monkeypatch, relations):
    monkeypatch.setattr(experiment.sys, "gettrace", lambda: None)
    monkeypatch.setattr(experiment.sys, "getprofile", lambda: None)
    monkeypatch.setitem(
        experiment.sys.modules,
        "coverage",
        SimpleNamespace(Coverage=SimpleNamespace(current=lambda: object())),
    )
    with pytest.raises(AssertionError, match="without coverage"):
        experiment.require_clean_runtime()

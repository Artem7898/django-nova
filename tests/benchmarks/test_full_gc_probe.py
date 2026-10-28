"""Fresh-process GC measurements, restoration, isolation and offline evidence guards."""

import gc
import hashlib
import json
import shutil
import subprocess
import sys
import weakref
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from benchmarks import full_gc_probe as worker
from scripts import run_full_gc_probe as runner
from scripts import summarize_full_gc_probe as analyzer

from tests.benchmarks.test_pytest_plugin_profile import load
from tests.benchmarks.test_pytest_plugin_profile import profiles as profiles
from tests.benchmarks.test_serializer_environment import matrix as matrix

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def single_thread_worker(monkeypatch):
    # Fault-injection units model a disposable worker. The parent pytest process
    # may retain executor threads from unrelated async tests or plugins.
    monkeypatch.setattr(worker, "threading", SimpleNamespace(enumerate=lambda: [object()]))


@pytest.fixture(scope="module")
def probes(profiles, tmp_path_factory):
    output = tmp_path_factory.mktemp("full-gc") / "matrix"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_full_gc_probe.py",
            "--reference-dir",
            str(profiles),
            "--output-dir",
            str(output),
            "--repeats",
            "1",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    logs = "\n".join(p.read_text() for p in output.glob("*.log"))
    assert result.returncode == 0, result.stdout + result.stderr + logs
    return output


def test_real_collection_in_four_fresh_workers(probes):
    manifest, reference, bundle, reports = load(probes)
    result = analyzer.analyze(probes)
    assert manifest["complete"] and result["complete"]
    assert len(reports) == len({r["process_id"] for r in reports}) == 4
    assert len(result["pairs"]) == 2
    for r in reports:
        c = r["configuration"]
        assert r["payload_sha256"] == bundle["payloads"]["without_instance"]["sha256"]
        assert r["probe"]["measured_collections"] == 1
        assert r["probe"]["explicit_collections"] == 2
        assert r["probe"]["states"]["restored"]["policy"] == reference["environment"]["gc_policy"]
        assert r["work"]["timed_loads"] == r["work"]["sql_count"] == r["work"]["snapshots"] == 0
        assert not r["runtime_after"]["census_imported"]
        assert r["profile_evidence"]["before"] == r["profile_evidence"]["after"]
        if c["profile"] == "django_only":
            assert r["plugins"] == [["pytest-django", reference["plugins"][-1][1]]]
        assert "operations" not in r and "inventory" not in r
        for scope in r["prefix_gc"].values():
            assert scope["end_ns"] <= r["probe"]["start_ns"]


def test_default_plan_is_balanced_and_has_no_timing_loop(probes):
    _, reference, _, _ = load(probes)
    jobs = runner.jobs_for(reference, 4, 17)
    assert len(jobs) == 16
    for validate in (False, True):
        cells = [j for j in jobs if j["validate"] == validate]
        assert [j["profile"] for j in cells[::2]] == [
            "autoload",
            "django_only",
            "autoload",
            "django_only",
        ]
    for left, right in zip(jobs[::2], jobs[1::2], strict=True):
        assert left["pair_id"] == right["pair_id"]
        assert [left["profile"], right["profile"]] == left["pair_order"]
        assert left["hint_variant"] == right["hint_variant"] == "without_instance"
        assert "operations" not in left


@pytest.mark.parametrize("failing_call", [1, 2])
@pytest.mark.usefixtures("single_thread_worker")
def test_exception_in_either_collection_restores_gc(monkeypatch, failing_call):
    old_policy = worker.common.policy()
    old_callbacks = tuple(gc.callbacks)
    calls = []

    def fail(generation):
        assert generation == 2 and not gc.isenabled()
        calls.append(generation)
        if len(calls) == failing_call:
            raise RuntimeError("injected collection failure")
        return 0

    monkeypatch.setattr(gc, "collect", fail)
    with pytest.raises(RuntimeError, match="injected"):
        worker.collect_pair()
    assert len(calls) == failing_call
    assert worker.common.policy() == old_policy
    assert tuple(gc.callbacks) == old_callbacks


@pytest.mark.usefixtures("single_thread_worker")
def test_external_callback_is_rejected_without_removing_it():
    def unrelated(phase, info):
        pass

    gc.callbacks.append(unrelated)
    try:
        with pytest.raises(AssertionError, match="callbacks"):
            worker.collect_pair()
        assert unrelated in gc.callbacks and gc.isenabled()
    finally:
        gc.callbacks.remove(unrelated)


@pytest.mark.usefixtures("single_thread_worker")
def test_disabled_gc_is_rejected_without_enabling_it():
    gc.disable()
    try:
        with pytest.raises(AssertionError, match="enabled GC"):
            worker.collect_pair()
        assert not gc.isenabled()
    finally:
        gc.enable()


def test_parallel_threads_are_rejected_before_collection(monkeypatch):
    before = worker.common.policy()
    callbacks = tuple(gc.callbacks)
    monkeypatch.setattr(
        worker, "threading", SimpleNamespace(enumerate=lambda: [object(), object()])
    )

    def forbidden(*args):
        raise AssertionError("Must reject threads before changing GC or collecting")

    monkeypatch.setattr(gc, "disable", forbidden)
    monkeypatch.setattr(gc, "collect", forbidden)
    with pytest.raises(AssertionError, match="one Python thread"):
        worker.collect_pair()
    assert worker.common.policy() == before
    assert tuple(gc.callbacks) == callbacks


@pytest.mark.parametrize("validate", [False, True])
def test_warmup_drops_every_result_before_return(validate, monkeypatch):
    refs, validations = [], []

    class Result(list):
        pass

    class Serializer:
        def loads(self, payload):
            result = Result()
            refs.append(weakref.ref(result))
            return result

    def fingerprint(result):
        validations.append(1)
        return []

    monkeypatch.setattr(worker.common, "fingerprint", fingerprint)
    worker.warmup(Serializer(), b"payload", [], {"warmups": 3, "validate": validate})
    assert len(refs) == 4 and all(ref() is None for ref in refs)
    assert len(validations) == (4 if validate else 1)


@pytest.mark.parametrize(
    "fault",
    [
        "elapsed",
        "generation",
        "collected",
        "returned",
        "extra_collection",
        "restore",
        "threshold",
        "frozen",
        "callbacks",
        "counts",
        "protocol",
        "threads",
        "missing_state",
        "sql",
        "workload",
        "sources",
        "environment",
        "payload",
        "prefix",
        "overlap",
        "plugins",
        "registration",
        "arguments",
        "drift",
        "mixed_timing",
        "census",
    ],
)
def test_invalid_evidence_is_rejected(probes, fault):
    manifest, reference, bundle, reports = load(probes)
    r = deepcopy(next(r for r in reports if r["configuration"]["profile"] == "django_only"))
    p = r["probe"]
    if fault == "elapsed":
        p["elapsed_ns"] += 1
    elif fault == "generation":
        p["states"]["after_measured"]["stats"][0]["collections"] += 1
    elif fault == "collected":
        p["states"]["after_measured"]["stats"][2]["collected"] += 1
    elif fault == "returned":
        p["measured_return"] += 1
    elif fault == "extra_collection":
        p["states"]["before_preparation"]["stats"][0]["collections"] += 1
    elif fault == "restore":
        p["states"]["restored"]["policy"]["enabled"] = False
    elif fault == "threshold":
        p["states"]["before_measured"]["policy"]["thresholds"][0] += 1
    elif fault == "frozen":
        p["states"]["after_measured"]["frozen_objects"] += 1
    elif fault == "callbacks":
        p["states"]["before_measured"]["callback_count"] = 1
    elif fault == "counts":
        p["states"]["before_measured"]["counts"][0] = -1
    elif fault == "protocol":
        p["measured_collections"] = 2
    elif fault == "threads":
        p["python_threads"] = 2
    elif fault == "missing_state":
        p["states"].pop("before_measured")
    elif fault == "sql":
        r["work"]["sql_count"] = 1
    elif fault == "workload":
        r["configuration"]["hint_variant"] = "original"
    elif fault == "sources":
        r["source_sha256"]["benchmarks/serializer_environment.py"] = "changed"
    elif fault == "environment":
        r["environment"]["python"] = "changed"
    elif fault == "payload":
        r["payload_sha256"] = "changed"
    elif fault == "prefix":
        r["prefix_gc"]["warmup"]["after"]["stats"][0]["collected"] += 1
    elif fault == "overlap":
        r["prefix_gc"]["warmup"]["end_ns"] = p["end_ns"]
    elif fault == "plugins":
        r["plugins"].append(["unwanted", "1"])
    elif fault == "registration":
        for e in r["profile_evidence"].values():
            e["registered_plugins"] = sorted([*e["registered_plugins"], ["other", "other.plugin"]])
    elif fault == "arguments":
        for e in r["profile_evidence"].values():
            e["arguments"].append("--cov=nova")
    elif fault == "drift":
        r["profile_evidence"]["after"]["root_is_project"] = False
    elif fault == "mixed_timing":
        r["operations"] = []
    else:
        r["runtime_after"]["census_imported"] = True
    with pytest.raises(ValueError):
        analyzer.validate_report(
            r, r["configuration"], reference, bundle, manifest["reference_sha256"]
        )


@pytest.mark.parametrize(
    "fault",
    ["incomplete", "missing", "order", "duplicate", "checksum", "reference", "escape", "command"],
)
def test_invalid_matrix(probes, tmp_path, fault):
    out = tmp_path / "copy"
    shutil.copytree(probes, out)
    m = json.loads((out / "manifest.json").read_text())
    if fault == "incomplete":
        m["complete"] = False
    elif fault == "missing":
        m["runs"].pop()
    elif fault == "order":
        m["plan"][:2] = reversed(m["plan"][:2])
    elif fault == "duplicate":
        m["runs"][1]["report"] = m["runs"][0]["report"]
    elif fault == "checksum":
        (out / m["runs"][0]["report"]).write_text("{}")
    elif fault == "reference":
        (out / "reference.json").write_text("{}")
    elif fault == "escape":
        m["runs"][0]["report"] = "../outside.json"
    else:
        m["runs"][0]["command"].append("--cov=nova")
    (out / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(ValueError):
        analyzer.analyze(out)


def test_nonempty_measurement_is_retained_and_not_averaged_as_empty(probes, tmp_path):
    out = tmp_path / "copy"
    shutil.copytree(probes, out)
    m = json.loads((out / "manifest.json").read_text())
    entry = m["runs"][0]
    path = out / entry["report"]
    r = json.loads(path.read_text())
    r["probe"]["measured_return"] += 1
    for name in ("after_measured", "restored"):
        r["probe"]["states"][name]["stats"][2]["collected"] += 1
    path.write_text(json.dumps(r))
    entry["report_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (out / "manifest.json").write_text(json.dumps(m))
    result = analyzer.analyze(out)
    assert result["complete"] and result["pairs"][0]["empty_pair_ratio"] is None
    summary = next(
        s
        for s in result["summaries"]
        if s["profile"] == r["configuration"]["profile"]
        and s["validate"] == r["configuration"]["validate"]
    )
    assert summary["all"]["n"] == summary["nonempty"]["n"] == 1
    assert summary["empty"]["n"] == 0 and summary["empty"]["mean_ms"] is None


def test_cli_and_no_overwrite(probes, tmp_path, capsys):
    path = tmp_path / "analysis.json"
    analyzer.main([str(probes), "--json", str(path)])
    assert "collect2=" in capsys.readouterr().out
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        analyzer.main([str(probes), "--json", str(path)])
    assert path.read_bytes() == before
    with pytest.raises(FileExistsError):
        runner.main(["--reference-dir", "unused", "--output-dir", str(probes)])


def test_failure_leaves_incomplete_manifest(probes, tmp_path, monkeypatch):
    _, reference, _, _ = load(probes)
    monkeypatch.setattr(
        runner, "prepare_reference", lambda directory: (reference, probes / "payloads")
    )
    monkeypatch.setattr(
        runner, "command_for", lambda profile: [sys.executable, "-c", "raise SystemExit(9)"]
    )
    out = tmp_path / "failed"
    with pytest.raises(RuntimeError, match="Worker failed"):
        runner.main(["--reference-dir", "unused", "--output-dir", str(out)])
    m = json.loads((out / "manifest.json").read_text())
    assert not m["complete"] and m["runs"][0]["returncode"] == 9


def test_profile_environment_is_sanitized(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVA_FULL_GC_JOB", "ambient")
    monkeypatch.setenv("PYTEST_PLUGINS", "ambient")
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    ref = {"environment": {"base_settings": "example.settings"}}
    for profile in ("autoload", "django_only"):
        env = runner.environment_for(ref, tmp_path, {"profile": profile})
        assert "NOVA_FULL_GC_JOB" not in env and "PYTEST_PLUGINS" not in env
        assert env.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") == (
            "1" if profile == "django_only" else None
        )
        assert runner.command_for(profile)[-1] == "benchmarks/full_gc_probe_test.py"

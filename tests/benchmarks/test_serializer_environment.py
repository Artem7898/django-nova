"""Process isolation, input parity and offline environment-comparison contracts."""

import gc
import hashlib
import json
import os
import shutil
import subprocess
import sys
import weakref
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from benchmarks import serializer_environment as worker
from scripts import run_serializer_environment as runner
from scripts import summarize_serializer_environment as analyzer

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def matrix(tmp_path_factory):
    output = tmp_path_factory.mktemp("environment") / "matrix"
    command = [
        sys.executable,
        "scripts/run_serializer_environment.py",
        "--output-dir",
        str(output),
        "--settings",
        "tests.example_project.settings",
        "--rows",
        "2",
        "--operations",
        "4",
        "--warmups",
        "1",
        "--repeats",
        "1",
    ]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    logs = "\n".join(path.read_text() for path in output.glob("*.log"))
    assert result.returncode == 0, result.stdout + result.stderr + logs
    return output


def load_matrix(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    bundle = json.loads((directory / "payloads/payloads.json").read_text())
    reports = [json.loads((directory / run["report"]).read_text()) for run in manifest["runs"]]
    return manifest, bundle, reports


def test_fresh_workers_share_exact_bytes_policy_sources_and_semantics(matrix):
    result = analyzer.analyze(matrix)
    assert len(result["pairs"]) == 4
    manifest, bundle, reports = load_matrix(matrix)
    assert len({report["process_id"] for report in reports}) == 8
    assert os.getpid() not in {report["process_id"] for report in reports}
    assert bundle["hint_control"]["articles"] == 2
    assert bundle["hint_control"]["tag_instances"] == 6
    for report in reports:
        config = report["configuration"]
        assert report["payload_sha256"] == bundle["payloads"][config["hint_variant"]]["sha256"]
        for side in ("runtime_before", "runtime_after"):
            modules = report[side]["pytest_modules"]
            assert ("pytest" in modules) == (config["worker"] == "pytest")
            if config["worker"] == "minimal":
                assert not modules
        assert report["summary"]["sql_count"] == 0
        assert len(report["operations"]) == 4
        assert report["environment"]["gc_policy"] == bundle["gc_policy"]
        assert report["environment"]["installed_apps"][-1] == "tests.cache.relations_app"
    assert [entry["configuration"] for entry in manifest["runs"]] == manifest["plan"]


def test_adjacent_pairs_alternate_order_with_balanced_repeats():
    args = SimpleNamespace(rows=100, operations=400, warmups=5, repeats=4, seed=41)
    jobs = runner.jobs_for(args)
    assert len(jobs) == 32
    cells = {}
    for index in range(0, len(jobs), 2):
        first, second = jobs[index : index + 2]
        assert {**first, "worker": second["worker"]} == second
        assert [first["worker"], second["worker"]] == first["pair_order"]
        cells.setdefault((first["hint_variant"], first["validate"]), []).append(first["worker"])
    assert len(cells) == 4
    assert all(order == ["minimal", "pytest", "minimal", "pytest"] for order in cells.values())
    assert jobs == runner.jobs_for(args)
    args.seed += 1
    assert {json.dumps(j, sort_keys=True) for j in jobs} != {
        json.dumps(j, sort_keys=True) for j in runner.jobs_for(args)
    }


def test_launcher_removes_coverage_and_ambient_plugin_overrides(monkeypatch, tmp_path):
    for key in (
        "COVERAGE_PROCESS_START",
        "COV_CORE_SOURCE",
        "PYTHONTRACEMALLOC",
        "PYTEST_PLUGINS",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        "NOVA_ENV_JOB",
        "PYTHONOPTIMIZE",
    ):
        monkeypatch.setenv(key, "unwanted")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--cov=nova")
    env = runner.child_environment("test.settings", tmp_path)
    for key in (
        "COVERAGE_PROCESS_START",
        "COV_CORE_SOURCE",
        "PYTHONTRACEMALLOC",
        "PYTEST_PLUGINS",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        "NOVA_ENV_JOB",
        "PYTHONOPTIMIZE",
    ):
        assert key not in env
    assert env["PYTEST_ADDOPTS"] == "" and env["PYTHONHASHSEED"] == "0"
    assert runner.worker_command("minimal") == [
        sys.executable,
        "-m",
        "benchmarks.serializer_environment",
    ]
    assert "--no-cov" in runner.worker_command("pytest")


@pytest.mark.parametrize("validate", [False, True])
def test_common_loop_drops_results_and_preserves_collector(monkeypatch, validate):
    seen = []
    validations = []

    class Result(list):
        pass

    def loads(payload):
        assert payload == b"fixed"
        if seen:
            assert seen[-1]() is None, "Previous result is still held"
        result = Result([1, 2])
        seen.append(weakref.ref(result))
        return result

    def fingerprint(result):
        validations.append(len(result))
        return list(result)

    def forbidden(*args, **kwargs):
        raise AssertionError("GC must not be changed or forced")

    before = (worker.policy(), gc.callbacks[:])
    monkeypatch.setattr(worker, "fingerprint", fingerprint)
    for name in ("collect", "disable", "enable", "set_threshold", "set_debug", "freeze"):
        monkeypatch.setattr(gc, name, forbidden)
    report = worker.timed_reads(
        SimpleNamespace(loads=loads),
        b"fixed",
        [1, 2],
        {"warmups": 2, "operations": 5, "validate": validate},
    )
    assert all(ref() is None for ref in seen)
    assert len(seen) == 8
    assert len(validations) == (8 if validate else 1)
    assert (worker.policy(), gc.callbacks) == before
    for sample in report["operations"]:
        assert sample["start_ns"] <= sample["end_ns"] <= sample["released_ns"]
        assert ("validation_end_ns" in sample) == validate


def test_callback_cleanup_on_serializer_failure(monkeypatch):
    callbacks = gc.callbacks[:]

    def loads(payload):
        raise RuntimeError("load failure")

    with pytest.raises(RuntimeError, match="load failure"):
        worker.timed_reads(
            SimpleNamespace(loads=loads),
            b"fixed",
            [],
            {"warmups": 1, "operations": 1, "validate": False},
        )
    assert gc.callbacks == callbacks


@pytest.mark.django_db
def test_sql_guard_rejects_a_query():
    from django.db import connection

    with (
        worker.no_sql(),
        connection.cursor() as cursor,
        pytest.raises(AssertionError, match="SQL is forbidden"),
    ):
        cursor.execute("SELECT 1")


def test_zero_bucket_has_no_latency_estimate_and_counts_all_events():
    empty = analyzer.event_summary([])
    assert empty["count"] == 0 and empty["total_ms"] == 0
    assert all(empty[key] is None for key in ("mean_ms", "median_ms", "p95_ms", "max_ms"))
    events = [
        {"start_ns": 0, "end_ns": n * 1_000_000, "collected": 0, "uncollectable": 0}
        for n in (20, 30, 40)
    ]
    summary = analyzer.event_summary(events)
    assert (summary["count"], summary["total_ms"], summary["mean_ms"], summary["p95_ms"]) == (
        3,
        90,
        30,
        40,
    )


def test_zero_gen2_selection_keeps_generations_and_scopes_separate():
    from tests.benchmarks.test_serializer_gc_analysis import event, report, scope

    source = report()
    source["gc"]["events"] = [
        event(11, 16, generation=1, collected=0),
        event(30, 40, collected=0),
        event(70, 90, collected=123),
    ]
    source["preparation_gc"] = scope([event(1, 31, collected=0)])
    source["operations"][0]["gc_overlap_ns"] = 5
    result = analyzer.metrics(source, 20)
    assert result["gen2"]["loop"]["all"]["count"] == 2
    assert result["gen2"]["loop"]["collected_zero"]["total_ms"] == 10 / 1e6
    assert result["gen2"]["loop"]["collected_nonzero"]["collected"] == 123
    assert result["gen2"]["preparation"]["collected_zero"]["total_ms"] == 30 / 1e6
    assert result["gen2"]["warmup"]["collected_zero"]["mean_ms"] is None


def test_worker_checks_bytes_before_unpickling(matrix, tmp_path):
    shutil.copytree(matrix / "payloads", tmp_path / "payloads")
    (tmp_path / "payloads/original.pickle").write_bytes(b"corrupt")

    def forbidden(payload):
        raise RuntimeError("Must check bytes first")

    with pytest.raises(AssertionError, match="Payload checksum mismatch"):
        worker.load_input(
            tmp_path / "payloads",
            {"rows": 2, "hint_variant": "original"},
            SimpleNamespace(loads=forbidden),
        )


@pytest.mark.parametrize(
    "fault",
    [
        "payload",
        "config",
        "semantics",
        "policy",
        "pytest_import",
        "sql",
        "version",
        "callbacks",
        "stats",
        "release",
        "source",
    ],
)
def test_rejects_invalid_worker_evidence(matrix, fault):
    manifest, bundle, reports = load_matrix(matrix)
    report = deepcopy(reports[0])
    config = deepcopy(report["configuration"])
    digest = manifest["payload_bundle_sha256"]
    reference = analyzer.validate_report(report, config, bundle, digest)
    if fault == "payload":
        report["payload_sha256"] = "bad"
    elif fault == "config":
        report["configuration"]["operations"] += 1
    elif fault == "semantics":
        report["hint_control"]["fields_and_loaded_tags_equal"] = False
    elif fault == "policy":
        report["environment"]["gc_policy"]["thresholds"][0] += 1
    elif fault == "pytest_import":
        report["runtime_after"]["pytest_modules"] = ["pytest"]
    elif fault == "sql":
        report["summary"]["sql_count"] = 1
    elif fault == "version":
        report["environment"]["django"] = "different"
    elif fault == "callbacks":
        report["gc"]["incomplete_gc_starts"] = 1
    elif fault == "stats":
        report["gc"]["after"]["stats"][2]["collected"] += 1
    elif fault == "release":
        report["operations"][0]["released_ns"] = 0
    else:
        report["source_sha256"] = {"changed": "source"}
    with pytest.raises(ValueError):
        analyzer.validate_report(report, config, bundle, digest, reference)


@pytest.mark.parametrize(
    "fault",
    [
        "incomplete",
        "missing",
        "pair_order",
        "overlap",
        "duplicate",
        "escape",
        "checksum",
        "payload",
    ],
)
def test_offline_analyzer_rejects_invalid_matrix(matrix, tmp_path, fault):
    directory = tmp_path / "copy"
    shutil.copytree(matrix, directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if fault == "incomplete":
        manifest["complete"] = False
    elif fault == "missing":
        manifest["runs"].pop()
    elif fault == "pair_order":
        manifest["plan"][:2] = reversed(manifest["plan"][:2])
    elif fault == "overlap":
        manifest["runs"][1]["launched_ns"] = manifest["runs"][0]["launched_ns"]
    elif fault == "duplicate":
        manifest["runs"][1]["report"] = manifest["runs"][0]["report"]
    elif fault == "escape":
        manifest["runs"][0]["report"] = "../outside.json"
    elif fault == "checksum":
        (directory / manifest["runs"][0]["report"]).write_text("{}")
    else:
        (directory / "payloads/original.pickle").write_bytes(b"different")
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        analyzer.analyze(directory)


def test_existing_report_and_run_directory_are_never_overwritten(matrix, tmp_path):
    output = tmp_path / "analysis.json"
    output.write_text("keep")
    with pytest.raises(FileExistsError):
        analyzer.main([str(matrix), "--json", str(output)])
    assert output.read_text() == "keep"
    before = hashlib.sha256((matrix / "manifest.json").read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        runner.main(["--output-dir", str(matrix)])
    assert hashlib.sha256((matrix / "manifest.json").read_bytes()).hexdigest() == before


def test_failed_preparation_leaves_incomplete_manifest(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(runner.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="Payload preparation failed"):
        runner.main(["--output-dir", str(tmp_path / "failed")])
    manifest = json.loads((tmp_path / "failed/manifest.json").read_text())
    assert manifest["complete"] is False and manifest["producer"]["returncode"] == 7
    assert not manifest["runs"]


def test_failed_worker_preserves_job_and_incomplete_matrix(matrix, tmp_path, monkeypatch):
    count = 0

    def fail_worker(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 1:
            shutil.copytree(matrix / "payloads", tmp_path / "failed/payloads")
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=9)

    monkeypatch.setattr(runner.subprocess, "run", fail_worker)
    with pytest.raises(RuntimeError, match="Worker failed"):
        runner.main(["--output-dir", str(tmp_path / "failed"), "--rows", "2"])
    manifest = json.loads((tmp_path / "failed/manifest.json").read_text())
    assert manifest["complete"] is False
    assert len(manifest["runs"]) == 1 and manifest["runs"][0]["returncode"] == 9
    assert len(list((tmp_path / "failed").glob("*.job.json"))) == 1


def test_reference_requires_same_bytes_settings_and_versions(matrix):
    manifest, bundle, _ = load_matrix(matrix)
    env = deepcopy(bundle["environment"])
    env["gc_debug"] = bundle["gc_policy"]["debug"]
    runs = [
        {
            "configuration": job,
            "returncode": 0,
            "payload_sha256": bundle["payloads"][job["hint_variant"]]["sha256"],
            "payload_bytes": bundle["payloads"][job["hint_variant"]]["bytes"],
        }
        for job in manifest["plan"]
    ]
    previous = {
        "complete": True,
        "experiment": "serializer_gc_matrix",
        "runs": runs,
        "expected_workers": len(runs),
        "reference": {"environment": env},
    }
    analyzer.check_reference(previous, bundle, manifest["plan"])
    for fault in ("payload", "version", "operations", "gc"):
        candidate = deepcopy(previous)
        if fault == "payload":
            candidate["runs"][0]["payload_sha256"] = "wrong"
        elif fault == "version":
            candidate["reference"]["environment"]["python"] = "other"
        elif fault == "operations":
            candidate["runs"][0]["configuration"]["operations"] += 1
        else:
            candidate["reference"]["environment"]["gc_thresholds"][0] += 1
        with pytest.raises(ValueError):
            analyzer.check_reference(candidate, bundle, manifest["plan"])

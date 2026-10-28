"""Terminal census ownership, collector restoration and fresh-worker contracts."""

import gc
import hashlib
import json
import shutil
import subprocess
import sys
import types
import weakref
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from benchmarks import gc_inventory as worker
from scripts import run_gc_inventory as runner
from scripts import summarize_gc_inventory as analyzer

from tests.benchmarks.test_serializer_environment import matrix as matrix

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def single_thread_worker(monkeypatch):
    # Keep local fault injection independent of the parent test process's
    # executor threads; fresh-process tests exercise the real thread guard.
    monkeypatch.setattr(worker, "threading", SimpleNamespace(enumerate=lambda: [object()]))


@pytest.fixture(scope="module")
def inventories(matrix, tmp_path_factory):
    output = tmp_path_factory.mktemp("gc-inventory") / "matrix"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_gc_inventory.py",
            "--reference-dir",
            str(matrix),
            "--output-dir",
            str(output),
            "--repeats",
            "1",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    logs = "\n".join(p.read_text() for p in output.glob("*.log"))
    assert result.returncode == 0, result.stdout + result.stderr + logs
    return output


def read_reports(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    reports = [json.loads((directory / entry["report"]).read_text()) for entry in manifest["runs"]]
    return manifest, reports


def test_census_does_not_invoke_user_object_hooks_or_retain_objects():
    class Hostile:
        def __getattribute__(self, name):
            raise AssertionError("Instance attributes must not be read")

        def __repr__(self):
            raise AssertionError("Instances must not be represented")

        def __sizeof__(self):
            raise AssertionError("Instances must not be sized")

    item = Hostile()
    reference = weakref.ref(item)

    def function():
        pass

    function.__module__ = "diagnostic.function"
    module = types.ModuleType("diagnostic.module")
    result = worker.count_objects(([item], [function], [Hostile, module]), set())
    assert result["generation_counts"] == [1, 1, 2]
    assert result["functions_by_declared_module"] == [
        {"label": "diagnostic.function", "generations": [0, 1, 0], "total": 1}
    ]
    assert result["module_objects_by_name"] == [
        {"label": "diagnostic.module", "generations": [0, 0, 1], "total": 1}
    ]
    json.dumps(result, allow_nan=False)
    del item
    assert reference() is None


def test_exclusions_and_duplicate_identity_are_explicit():
    a, b = [], {}
    result = worker.count_objects(([a, b], [], []), {id(a)})
    assert result["generation_counts"] == [1, 0, 0]
    assert result["excluded_observer_counts"] == [1, 0, 0]
    with pytest.raises(AssertionError, match="multiple inventory positions"):
        worker.count_objects(([b], [b], []), set())


@pytest.mark.usefixtures("single_thread_worker")
def test_snapshot_suspends_only_inspection_and_restores_gc(monkeypatch):
    before = worker.common.policy()
    order, lists = [], {}
    samples = [SimpleNamespace(), SimpleNamespace(), SimpleNamespace()]

    def get_objects(generation):
        assert not gc.isenabled()
        order.append(generation)
        result = [samples[generation]]
        if generation == 0:
            result.extend(lists.values())
        lists[generation] = result
        return result

    def forbidden(*args, **kwargs):
        raise AssertionError("No collection, retuning, freeze or unfreeze")

    monkeypatch.setattr(gc, "get_objects", get_objects)
    for name in ("collect", "set_threshold", "set_debug", "freeze", "unfreeze"):
        monkeypatch.setattr(gc, name, forbidden)
    result = worker.take_inventory()
    assert order == [2, 1, 0]
    assert worker.common.policy() == before
    assert result["generation_counts"] == [1, 1, 1]
    assert result["excluded_observer_counts"] == [2, 0, 0]
    assert result["inspection"]["stats_before"] == result["inspection"]["stats_after_aggregation"]
    analyzer.validate_inventory(result, before)


@pytest.mark.usefixtures("single_thread_worker")
def test_failed_snapshot_restores_automatic_gc(monkeypatch):
    before = worker.common.policy()

    def fail(*args):
        assert not gc.isenabled()
        raise RuntimeError("census failure")

    monkeypatch.setattr(worker, "count_objects", fail)
    with pytest.raises(RuntimeError, match="census failure"):
        worker.take_inventory()
    assert worker.common.policy() == before


def test_parallel_threads_are_rejected_before_suspension(monkeypatch):
    before = worker.common.policy()
    monkeypatch.setattr(worker.threading, "enumerate", lambda: [object(), object()])
    with pytest.raises(AssertionError, match="one Python thread"):
        worker.take_inventory()
    assert worker.common.policy() == before


@pytest.mark.parametrize("validate", [False, True])
def test_warmup_has_the_original_load_validate_dispose_schedule(monkeypatch, validate):
    references, validations = [], []

    class Result(list):
        pass

    def loads(payload):
        assert payload == b"fixed"
        assert not references or references[-1]() is None
        result = Result([1, 2])
        references.append(weakref.ref(result))
        return result

    def fingerprint(result):
        validations.append(len(result))
        return list(result)

    def forbidden(*args, **kwargs):
        raise AssertionError("Warmup must not inspect or change the collector")

    monkeypatch.setattr(worker.common, "fingerprint", fingerprint)
    for name in (
        "get_objects",
        "collect",
        "disable",
        "enable",
        "set_threshold",
        "freeze",
        "unfreeze",
    ):
        monkeypatch.setattr(gc, name, forbidden)
    before = (worker.common.policy(), gc.callbacks[:])
    worker.warmup(
        SimpleNamespace(loads=loads), b"fixed", [1, 2], {"warmups": 5, "validate": validate}
    )
    assert len(references) == 6 and all(ref() is None for ref in references)
    assert len(validations) == (6 if validate else 1)
    assert (worker.common.policy(), gc.callbacks) == before


def test_fresh_checkpoint_workers_and_offline_deltas(inventories):
    result = analyzer.analyze(inventories)
    manifest, reports = read_reports(inventories)
    assert len(reports) == 10 and len({r["process_id"] for r in reports}) == 10
    assert len(result["pairs"]) == 5 and len(result["warmup_deltas"]) == 8
    for delta in result["warmup_deltas"]:
        assert delta["startup_process_id"] != delta["warmup_process_id"]
    for report in reports:
        config = report["configuration"]
        work = report["work"]
        assert work["snapshots"] == 1 and work["timed_operations"] == work["sql_count"] == 0
        if config["checkpoint"] == "startup":
            assert work["warmup_loads"] == work["preflight_loads"] == 0
            assert not report["prefix_gc"]
        else:
            assert work["warmup_loads"] == config["warmups"] + 1
        if config["worker"] == "minimal":
            assert not report["plugins"] and not report["runtime_after"]["pytest_modules"]
        else:
            assert report["plugins"] and "pytest" in report["runtime_after"]["pytest_modules"]
        inspection = report["inventory"]["inspection"]
        assert inspection["policy_before"] == inspection["policy_restored"]
        assert (
            inspection["stats_before"]
            == inspection["stats_after_acquisition"]
            == inspection["stats_after_aggregation"]
        )
    assert manifest["complete"]


def test_default_plan_balances_adjacent_process_order():
    reference = {"workload": {"rows": 100, "warmups": 5}}
    jobs = runner.jobs_for(reference, 4, 7)
    assert len(jobs) == 40
    starts = {}
    for i in range(0, len(jobs), 2):
        first, second = jobs[i : i + 2]
        assert {**first, "worker": second["worker"]} == second
        assert [first["worker"], second["worker"]] == first["pair_order"]
        cell = (first["checkpoint"], first["hint_variant"], first["validate"])
        starts.setdefault(cell, []).append(first["worker"])
    assert len(starts) == 5
    assert all(order == ["minimal", "pytest", "minimal", "pytest"] for order in starts.values())


def test_count_delta_includes_promotions_with_zero_total():
    left = {"generation_counts": [2, 0, 0], "total": 2, **{t: [] for t in analyzer.TABLES}}
    right = {"generation_counts": [0, 0, 2], "total": 2, **{t: [] for t in analyzer.TABLES}}
    left["types"] = [{"label": "sample.Model", "generations": [2, 0, 0], "total": 2}]
    right["types"] = [{"label": "sample.Model", "generations": [0, 0, 2], "total": 2}]
    delta = analyzer.difference(left, right)
    assert delta["total"] == 0 and delta["generation_counts"] == [-2, 0, 2]
    assert delta["types"][0]["generations"] == [-2, 0, 2]


@pytest.mark.parametrize(
    "fault",
    [
        "counts",
        "type",
        "duplicate",
        "gc",
        "restoration",
        "frozen",
        "exclusions",
        "sources",
        "versions",
        "snapshot_count",
        "sql",
        "plugins",
    ],
)
def test_rejects_invalid_inventory_evidence(inventories, fault):
    manifest, reports = read_reports(inventories)
    report = deepcopy(reports[0])
    reference = json.loads((inventories / "reference.json").read_text())
    bundle = json.loads((inventories / "payloads/payloads.json").read_text())
    config = deepcopy(report["configuration"])
    sources = deepcopy(report["source_sha256"])
    inv = report["inventory"]
    if fault == "counts":
        inv["generation_counts"][0] += 1
    elif fault == "type":
        inv["types"][0]["generations"][1] += 1
    elif fault == "duplicate":
        inv["types"].append(inv["types"][0])
    elif fault == "gc":
        inv["inspection"]["stats_after_aggregation"][0]["collections"] += 1
    elif fault == "restoration":
        inv["inspection"]["policy_restored"]["enabled"] = False
    elif fault == "frozen":
        inv["inspection"]["frozen_objects_after"] += 1
    elif fault == "exclusions":
        inv["excluded_observer_counts"][0] += 1
    elif fault == "sources":
        report["source_sha256"]["benchmarks/gc_inventory.py"] = "different"
    elif fault == "versions":
        report["environment"]["python"] = "different"
    elif fault == "snapshot_count":
        report["work"]["snapshots"] = 2
    elif fault == "sql":
        report["work"]["sql_count"] = 1
    else:
        report["plugins"] = [["unexpected", "1"]]
    with pytest.raises(ValueError):
        analyzer.validate_report(
            report, config, reference, bundle, manifest["reference_sha256"], sources
        )


@pytest.mark.parametrize(
    "fault", ["incomplete", "missing", "order", "duplicate", "escape", "checksum", "reference"]
)
def test_matrix_integrity_checks(inventories, tmp_path, fault):
    target = tmp_path / "matrix"
    shutil.copytree(inventories, target)
    manifest = json.loads((target / "manifest.json").read_text())
    if fault == "incomplete":
        manifest["complete"] = False
    elif fault == "missing":
        manifest["runs"].pop()
    elif fault == "order":
        manifest["plan"][:2] = reversed(manifest["plan"][:2])
    elif fault == "duplicate":
        manifest["runs"][1]["report"] = manifest["runs"][0]["report"]
    elif fault == "escape":
        manifest["runs"][0]["report"] = "../escape.json"
    elif fault == "checksum":
        (target / manifest["runs"][0]["report"]).write_text("{}")
    else:
        (target / "reference.json").write_text("{}")
    (target / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        analyzer.analyze(target)


def test_existing_output_is_not_overwritten(inventories, tmp_path):
    before = (inventories / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        runner.main(["--output-dir", str(inventories), "--reference-dir", "unused"])
    assert (inventories / "manifest.json").read_bytes() == before
    output = tmp_path / "analysis.json"
    output.write_text("keep")
    with pytest.raises(FileExistsError):
        analyzer.main([str(inventories), "--json", str(output)])
    assert output.read_text() == "keep"


def test_failure_leaves_incomplete_manifest_and_preserves_timing_sources(
    inventories, tmp_path, monkeypatch
):
    reference = json.loads((inventories / "reference.json").read_text())
    hashes = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in reference["source_sha256"]
    }
    monkeypatch.setattr(
        runner, "prepare_reference", lambda path: (reference, inventories / "payloads")
    )
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=9)
    )
    with pytest.raises(RuntimeError, match="Worker failed"):
        runner.main(
            ["--output-dir", str(tmp_path / "failure"), "--reference-dir", str(inventories)]
        )
    manifest = json.loads((tmp_path / "failure/manifest.json").read_text())
    assert not manifest["complete"] and manifest["runs"][0]["returncode"] == 9
    assert hashes == {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in hashes
    }


@pytest.mark.parametrize("name", ["../escape.pkl", "/tmp/escape.pkl"])
def test_payload_copy_cannot_escape_output(inventories, tmp_path, monkeypatch, name):
    payloads = tmp_path / "inputs"
    shutil.copytree(inventories / "payloads", payloads)
    bundle = json.loads((payloads / "payloads.json").read_text())
    bundle["payloads"]["original"]["file"] = name
    (payloads / "payloads.json").write_text(json.dumps(bundle))
    reference = json.loads((inventories / "reference.json").read_text())
    monkeypatch.setattr(runner, "prepare_reference", lambda path: (reference, payloads))
    with pytest.raises(ValueError, match="payload basename"):
        runner.main(["--reference-dir", "unused", "--output-dir", str(tmp_path / "output")])
    assert not json.loads((tmp_path / "output/manifest.json").read_text())["complete"]

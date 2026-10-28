"""Preparation intervention, unchanged read boundaries and paired report integrity."""

import ast
import gc
import inspect
import json
import shutil
import subprocess
import sys
import weakref
from copy import deepcopy
from pathlib import Path

import pytest
from benchmarks import gc_preparation as worker
from scripts import run_gc_preparation as runner
from scripts import summarize_gc_preparation as analyzer

from tests.benchmarks.test_pytest_plugin_profile import load
from tests.benchmarks.test_pytest_plugin_profile import profiles as profiles
from tests.benchmarks.test_serializer_environment import matrix as matrix

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def prepared_matrix(profiles, tmp_path_factory):
    output = tmp_path_factory.mktemp("gc-preparation") / "matrix"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_gc_preparation.py",
            "--reference-dir",
            str(profiles),
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


def test_actual_eight_workers_keep_payload_policy_and_exclude_preparation(prepared_matrix):
    manifest, reference, bundle, reports = load(prepared_matrix)
    result = analyzer.analyze(prepared_matrix)
    assert manifest["complete"] and result["complete"]
    assert len(reports) == len({r["process_id"] for r in reports}) == 8
    assert len(result["pairs"]) == len(result["profile_comparisons"]) == 4
    for r in reports:
        c = r["configuration"]
        assert len(r["operations"]) == c["operations"] == reference["workload"]["operations"]
        assert r["payload_sha256"] == bundle["payloads"]["without_instance"]["sha256"]
        assert r["summary"]["sql_count"] == 0
        assert r["gc_guard_before"] == r["gc_guard_after"]
        assert r["gc_guard_after"]["policy"] == reference["environment"]["gc_policy"]
        assert r["profile_evidence"]["before"] == r["profile_evidence"]["after"]
        call = r["conditioning_call"]
        assert (
            r["warmup_gc"]["end_ns"]
            <= call["start_ns"]
            <= call["end_ns"]
            < r["operations"][0]["start_ns"]
        )
        assert (call["returned"] is not None) == c["prepare_gc"]
        m = analyzer.metrics(r, 20)
        assert (m["explicit_preparation_ms"] is not None) == c["prepare_gc"]
        if c["prepare_gc"]:
            assert m["explicit_preparation_ms"] == (call["end_ns"] - call["start_ns"]) / 1e6
        assert len(m["gen2_positions"]) == m["loop_gen2_count"]
        assert m["loop_gc_ms"] == sum(g["duration_ns"] for g in m["scopes"]["loop"]) / 1e6
        assert "inventory" not in r and not r["runtime_after"]["census_imported"]


def test_plan_has_adjacent_preparation_pairs_and_balanced_orders(prepared_matrix):
    _, reference, _, _ = load(prepared_matrix)
    jobs = runner.jobs_for(reference, 4, 41)
    assert len(jobs) == 32
    for validation in (False, True):
        block = [j for j in jobs if j["validate"] == validation]
        assert [j["profile"] for j in block[::4]] == [
            "autoload",
            "django_only",
            "django_only",
            "autoload",
        ]
        for profile in ("autoload", "django_only"):
            pairs = [j for j in block if j["profile"] == profile]
            assert [j["prepare_gc"] for j in pairs[::2]] == [False, True, False, True]
    for a, b in zip(jobs[::2], jobs[1::2], strict=True):
        assert a["profile"] == b["profile"] and a["pair_id"] == b["pair_id"]
        assert {**a, "prepare_gc": b["prepare_gc"]} == b
        assert [a["prepare_gc"], b["prepare_gc"]] == a["pair_order"]


def test_warmup_and_measured_loop_ast_match_previous_boundaries():
    def loops(function):
        tree = ast.parse(inspect.getsource(function))
        return [
            ast.dump(node, include_attributes=False)
            for node in ast.walk(tree)
            if isinstance(node, ast.For)
        ]

    # Protect the actual performance boundary, including validation, del result,
    # released_ns and the placement of per-operation observer allocations.
    assert loops(worker.timed_reads) == loops(worker.common.timed_reads)


@pytest.mark.parametrize("prepare", [False, True])
@pytest.mark.parametrize("validate", [False, True])
def test_one_optional_collection_after_disposal_and_before_reads(monkeypatch, prepare, validate):
    refs, validations, calls = [], [], []
    policy, callbacks = worker.common.policy(), tuple(gc.callbacks)

    class Result(list):
        pass

    class Serializer:
        def loads(self, payload):
            assert all(ref() is None for ref in refs), "Prior restored rows are retained"
            result = Result()
            refs.append(weakref.ref(result))
            return result

    def fingerprint(result):
        validations.append(len(refs))
        return []

    def collect(generation):
        assert generation == 2 and gc.isenabled()
        assert len(refs) == 4 and all(ref() is None for ref in refs)
        assert worker.common.policy() == policy
        calls.append(generation)
        return 7

    def forbidden(*args):
        raise AssertionError("Must not change GC policy")

    monkeypatch.setattr(worker, "fingerprint", fingerprint)
    monkeypatch.setattr(gc, "collect", collect)
    for name in ("disable", "enable", "set_threshold", "set_debug", "freeze", "unfreeze"):
        monkeypatch.setattr(gc, name, forbidden)
    result = worker.timed_reads(
        Serializer(),
        b"fixed",
        [],
        {
            "warmups": 3,
            "operations": 5,
            "validate": validate,
            "prepare_gc": prepare,
        },
    )
    assert len(refs) == 9 and all(ref() is None for ref in refs)
    assert len(validations) == (9 if validate else 1)
    assert calls == ([2] if prepare else [])
    assert result["conditioning_call"]["returned"] == (7 if prepare else None)
    assert result["conditioning_gc"]["end_ns"] <= result["gc"]["start_ns"]
    assert tuple(gc.callbacks) == callbacks and worker.common.policy() == policy


@pytest.mark.parametrize("failure", ["collect", "loads", "validation"])
def test_failure_removes_own_callbacks_and_preserves_policy(monkeypatch, failure):
    calls = []
    policy, callbacks = worker.common.policy(), tuple(gc.callbacks)

    class Serializer:
        def loads(self, payload):
            calls.append(1)
            if failure == "loads" and len(calls) == 3:
                raise RuntimeError("injected loads")
            return []

    def fingerprint(result):
        if failure == "validation" and len(calls) == 3:
            raise RuntimeError("injected validation")
        return []

    def collect(generation):
        raise RuntimeError("injected collect")

    monkeypatch.setattr(worker, "fingerprint", fingerprint)
    if failure == "collect":
        monkeypatch.setattr(gc, "collect", collect)
    with pytest.raises(RuntimeError, match="injected"):
        worker.timed_reads(
            Serializer(),
            b"fixed",
            [],
            {
                "warmups": 1,
                "operations": 3,
                "validate": True,
                "prepare_gc": failure == "collect",
            },
        )
    assert tuple(gc.callbacks) == callbacks and worker.common.policy() == policy


def test_position_classification_includes_gc_between_and_outside_reads():
    report = {
        "operations": [
            {
                "start_ns": 10,
                "end_ns": 20,
                "validation_start_ns": 22,
                "validation_end_ns": 25,
                "released_ns": 30,
            },
            {"start_ns": 40, "end_ns": 50, "released_ns": 60},
        ],
        "gc": {
            "start_ns": 0,
            "events": [
                {
                    "start_ns": t,
                    "end_ns": t + 1,
                    "generation": 2,
                    "collected": 0,
                    "uncollectable": 0,
                }
                for t in (5, 11, 23, 26, 35, 41, 51, 65)
            ],
        },
    }
    positions = analyzer.gen2_positions(report)
    assert [p["phase"] for p in positions] == [
        "before_first_read",
        "loads",
        "validation",
        "post_load_or_disposal",
        "between_reads",
        "loads",
        "post_load_or_disposal",
        "after_last_read",
    ]
    assert [p["operation_index"] for p in positions] == [None, 0, 0, 0, None, 1, 1, None]
    assert positions[4]["previous_index"] == 0 and positions[4]["next_index"] == 1
    assert positions[-1]["next_index"] is None


@pytest.mark.parametrize(
    "fault",
    [
        "elapsed",
        "released",
        "order",
        "validation",
        "overlap",
        "sql",
        "operations",
        "conditioning_stats",
        "conditioning_generation",
        "returned",
        "requested",
        "control_return",
        "conditioning_overlap",
        "enabled",
        "thresholds",
        "guard",
        "callbacks",
        "frozen",
        "source",
        "environment",
        "payload",
        "plugins",
        "registration",
        "arguments",
        "drift",
        "census",
    ],
)
def test_invalid_worker_evidence_is_rejected(prepared_matrix, fault):
    manifest, reference, bundle, reports = load(prepared_matrix)
    r = deepcopy(
        next(
            r
            for r in reports
            if r["configuration"]["profile"] == "django_only"
            and r["configuration"]["prepare_gc"] == (fault != "control_return")
        )
    )
    if fault == "elapsed":
        r["operations"][0]["elapsed_ns"] += 1
    elif fault == "released":
        r["operations"][0]["released_ns"] = 0
    elif fault == "order":
        r["operations"][0]["index"] += 1
    elif fault == "validation":
        r["summary"]["validation_calls"] += 1
    elif fault == "overlap":
        r["operations"][0]["gc_overlap_ns"] += 1
    elif fault == "sql":
        r["summary"]["sql_count"] = 1
    elif fault == "operations":
        r["operations"].pop()
    elif fault == "conditioning_stats":
        r["conditioning_gc"]["after"]["stats"][2]["collected"] += 1
    elif fault == "conditioning_generation":
        r["conditioning_gc"]["events"][0]["generation"] = 0
    elif fault == "returned":
        r["conditioning_call"]["returned"] += 1
    elif fault == "requested":
        r["conditioning_call"]["requested"] = False
    elif fault == "control_return":
        r["conditioning_call"]["returned"] = 0
    elif fault == "conditioning_overlap":
        r["conditioning_call"]["end_ns"] = r["gc"]["end_ns"]
    elif fault == "enabled":
        r["gc"]["before"]["enabled"] = False
    elif fault == "thresholds":
        r["conditioning_gc"]["before"]["thresholds"][0] += 1
    elif fault == "guard":
        r["gc_guard_after"]["policy"]["debug"] = 1
    elif fault == "callbacks":
        r["gc_guard_after"]["callbacks"] = 1
    elif fault == "frozen":
        r["gc_guard_after"]["frozen"] += 1
    elif fault == "source":
        r["source_sha256"]["benchmarks/serializer_environment.py"] = "changed"
    elif fault == "environment":
        r["environment"]["python"] = "changed"
    elif fault == "payload":
        r["payload_sha256"] = "changed"
    elif fault == "plugins":
        r["plugins"] = []
    elif fault == "registration":
        r["profile_evidence"]["before"]["registered_plugins"].append(["unwanted", "third_party"])
    elif fault == "arguments":
        r["profile_evidence"]["before"]["arguments"].append("--cov=nova")
    elif fault == "drift":
        r["profile_evidence"]["after"]["root_is_project"] = False
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
def test_invalid_matrix(prepared_matrix, tmp_path, fault):
    out = tmp_path / "copy"
    shutil.copytree(prepared_matrix, out)
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


def test_cli_and_no_overwrite(prepared_matrix, tmp_path, capsys):
    path = tmp_path / "analysis.json"
    analyzer.main([str(prepared_matrix), "--json", str(path)])
    assert "gen2_positions=" in capsys.readouterr().out
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        analyzer.main([str(prepared_matrix), "--json", str(path)])
    assert path.read_bytes() == before
    with pytest.raises(FileExistsError):
        runner.main(["--reference-dir", "unused", "--output-dir", str(prepared_matrix)])
    with pytest.raises(ValueError, match="slow-ms"):
        analyzer.analyze(prepared_matrix, float("nan"))


def test_failure_leaves_incomplete_manifest(prepared_matrix, tmp_path, monkeypatch):
    _, reference, _, _ = load(prepared_matrix)
    monkeypatch.setattr(
        runner, "prepare_reference", lambda directory: (reference, prepared_matrix / "payloads")
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
    for key in (
        "NOVA_GC_PREP_JOB",
        "NOVA_FULL_GC_JOB",
        "PYTEST_PLUGINS",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
    ):
        monkeypatch.setenv(key, "ambient")
    ref = {"environment": {"base_settings": "example.settings"}}
    for profile in ("autoload", "django_only"):
        env = runner.environment_for(ref, tmp_path, {"profile": profile})
        assert (
            "NOVA_GC_PREP_JOB" not in env
            and "NOVA_FULL_GC_JOB" not in env
            and "PYTEST_PLUGINS" not in env
        )
        assert env.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") == (
            "1" if profile == "django_only" else None
        )
        assert runner.command_for(profile)[-1] == "benchmarks/gc_preparation_test.py"

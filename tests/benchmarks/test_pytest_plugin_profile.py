"""Actual plugin isolation and independent timing/inventory process contracts."""

import hashlib
import json
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from benchmarks import pytest_plugin_profile as worker
from scripts import run_pytest_plugin_profile as runner
from scripts import summarize_pytest_plugin_profile as analyzer

from tests.benchmarks.test_serializer_environment import matrix as matrix

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def profiles(matrix, tmp_path_factory):
    output = tmp_path_factory.mktemp("plugin-profiles") / "matrix"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_pytest_plugin_profile.py",
            "--reference-dir",
            str(matrix),
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


def load(directory):
    manifest = json.loads((directory / "manifest.json").read_text())
    reference = json.loads((directory / "reference.json").read_text())
    bundle = json.loads((directory / "payloads/payloads.json").read_text())
    reports = [json.loads((directory / entry["report"]).read_text()) for entry in manifest["runs"]]
    return manifest, reference, bundle, reports


def test_actual_plugin_profiles_in_fresh_separate_workers(profiles):
    manifest, reference, _, reports = load(profiles)
    result = analyzer.analyze(profiles)
    assert manifest["complete"] and len(reports) == len({r["process_id"] for r in reports}) == 18
    assert len(result["pairs"]) == 9 and len(result["warmup_deltas"]) == 8
    for report in reports:
        job = report["configuration"]
        before = report["profile_evidence"]["before"]
        assert before == report["profile_evidence"]["after"]
        modules = {module for _, module in before["registered_plugins"]}
        assert "pytest_django.plugin" in modules
        if job["profile"] == "django_only":
            assert report["plugins"] == [
                ["pytest-django", dict(reference["plugins"])["pytest-django"]]
            ]
            assert before["autoload_disabled"]
            assert not modules & {
                "anyio.pytest_plugin",
                "pytest_cov.plugin",
                "pytest_asyncio.plugin",
                "pytest_benchmark.plugin",
            }
        else:
            assert report["plugins"] == reference["plugins"]
            assert not before["autoload_disabled"]
            assert "pytest_asyncio.plugin" in modules and before["asyncio_mode"] == "auto"
        if job["mode"] == "timing":
            assert not report["runtime_after"]["census_imported"]
            assert "inventory" not in report and len(report["operations"]) == job["operations"]
            assert report["summary"]["sql_count"] == 0
        else:
            assert report["runtime_after"]["census_imported"] and "operations" not in report
            assert report["work"]["timed_operations"] == report["work"]["sql_count"] == 0
            assert report["work"]["snapshots"] == 1
            inspection = report["inventory"]["inspection"]
            assert inspection["stats_before"] == inspection["stats_after_aggregation"]
            assert inspection["policy_before"] == inspection["policy_restored"]
    for delta in result["warmup_deltas"]:
        assert delta["startup_process_id"] != delta["warmup_process_id"]


def test_profile_is_set_after_inherited_environment_is_cleared(monkeypatch, tmp_path):
    for key in (
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        "PYTEST_PLUGINS",
        "PYTEST_ADDOPTS",
        "COVERAGE_PROCESS_START",
        "NOVA_GC_JOB",
        "NOVA_PLUGIN_JOB",
    ):
        monkeypatch.setenv(key, "ambient-unwanted")
    reference = {"environment": {"base_settings": "test.settings"}}
    for profile in ("autoload", "django_only"):
        env = runner.environment_for(reference, tmp_path, {"profile": profile})
        assert env.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD") == (
            "1" if profile == "django_only" else None
        )
        assert env["PYTEST_ADDOPTS"] == ""
        assert (
            not {"PYTEST_PLUGINS", "COVERAGE_PROCESS_START", "NOVA_GC_JOB", "NOVA_PLUGIN_JOB"}
            & env.keys()
        )
        assert env["PYTHONHASHSEED"] == "0"
        command = runner.command_for(profile)
        assert "--no-cov" not in command
        assert command[command.index("-c") + 1] == "pytest.plugin-profile.ini"
        assert ("django" in command) == (profile == "django_only")


def test_class_plugin_is_identified_by_its_declared_module():
    from _pytest.legacypath import LegacyTmpdirPlugin

    manager = SimpleNamespace(
        list_name_plugin=lambda: [("legacypath-tmpdir", LegacyTmpdirPlugin)],
        list_plugin_distinfo=lambda: [],
        hasplugin=lambda name: False,
    )
    config = SimpleNamespace(
        pluginmanager=manager,
        invocation_params=SimpleNamespace(args=[]),
        inipath=ROOT / "pytest.plugin-profile.ini",
        rootpath=ROOT,
    )
    evidence = worker.plugin_evidence(config)
    assert evidence["registered_plugins"] == [["legacypath-tmpdir", "_pytest.legacypath"]]


def test_balanced_72_worker_plan(profiles):
    _, reference, _, _ = load(profiles)
    jobs = runner.jobs_for(reference, 4, 7)
    assert len(jobs) == 72
    assert sum(j["mode"] == "timing" for j in jobs) == 32
    cells = {}
    for i in range(0, len(jobs), 2):
        first, second = jobs[i : i + 2]
        assert {k: v for k, v in first.items() if k not in ("profile", "expected_plugins")} == {
            k: v for k, v in second.items() if k not in ("profile", "expected_plugins")
        }
        assert [first["profile"], second["profile"]] == first["pair_order"]
        cells.setdefault((first["mode"], first["hint_variant"], first["validate"]), []).append(
            first["profile"]
        )
    assert len(cells) == 9
    assert all(
        order == ["autoload", "django_only", "autoload", "django_only"] for order in cells.values()
    )


@pytest.mark.parametrize(
    "fault",
    [
        "autoload",
        "distribution",
        "registration",
        "django_missing",
        "arguments",
        "config",
        "asyncio",
        "after",
        "expected",
        "source",
        "environment",
        "sql",
        "snapshot_import",
        "mixed_inventory",
        "count",
    ],
)
def test_invalid_timing_or_plugin_evidence_is_rejected(profiles, fault):
    manifest, reference, bundle, reports = load(profiles)
    report = deepcopy(
        next(
            r
            for r in reports
            if r["configuration"]["profile"] == "django_only"
            and r["configuration"]["mode"] == "timing"
        )
    )
    job = deepcopy(report["configuration"])
    sources = deepcopy(report["source_sha256"])
    evidence = report["profile_evidence"]
    if fault == "autoload":
        evidence["before"]["autoload_disabled"] = evidence["after"]["autoload_disabled"] = False
    elif fault == "distribution":
        report["plugins"].append(["unwanted", "1"])
    elif fault == "registration":
        for item in evidence.values():
            item["registered_plugins"] = sorted(
                [*item["registered_plugins"], ["unlisted", "unlisted.plugin"]]
            )
    elif fault == "django_missing":
        for item in evidence.values():
            item["registered_plugins"] = [
                entry for entry in item["registered_plugins"] if entry[1] != "pytest_django.plugin"
            ]
    elif fault == "arguments":
        for item in evidence.values():
            item["arguments"].append("--cov=nova")
    elif fault == "config":
        for item in evidence.values():
            item["config_file"] = "pyproject.toml"
    elif fault == "asyncio":
        for item in evidence.values():
            item["asyncio_mode"] = "auto"
    elif fault == "after":
        evidence["after"]["root_is_project"] = False
    elif fault == "expected":
        job["expected_plugins"] = report["configuration"]["expected_plugins"] = []
    elif fault == "source":
        report["source_sha256"]["benchmarks/serializer_environment.py"] = "changed"
    elif fault == "environment":
        report["environment"]["python"] = "changed"
    elif fault == "sql":
        report["summary"]["sql_count"] = 1
    elif fault == "snapshot_import":
        report["runtime_after"]["census_imported"] = True
    elif fault == "mixed_inventory":
        report["inventory"] = {}
    else:
        report["operations"].pop()
    with pytest.raises(ValueError):
        analyzer.validate_report(
            report, job, reference, bundle, manifest["reference_sha256"], sources
        )


@pytest.mark.parametrize(
    "fault", ["timing", "gc_restore", "gc_collected", "snapshot_count", "missing_census"]
)
def test_inventory_guards_are_preserved(profiles, fault):
    manifest, reference, bundle, reports = load(profiles)
    report = deepcopy(next(r for r in reports if r["configuration"]["mode"] == "after_warmup"))
    if fault == "timing":
        report["operations"] = []
    elif fault == "gc_restore":
        report["inventory"]["inspection"]["policy_restored"]["enabled"] = False
    elif fault == "gc_collected":
        report["inventory"]["inspection"]["stats_after_aggregation"][2]["collections"] += 1
    elif fault == "snapshot_count":
        report["work"]["snapshots"] = 2
    else:
        report["runtime_after"]["census_imported"] = False
    with pytest.raises(ValueError):
        analyzer.validate_report(
            report, report["configuration"], reference, bundle, manifest["reference_sha256"]
        )


@pytest.mark.parametrize(
    "fault",
    ["incomplete", "missing", "order", "duplicate", "escape", "checksum", "reference", "command"],
)
def test_matrix_integrity(profiles, tmp_path, fault):
    output = tmp_path / "matrix"
    shutil.copytree(profiles, output)
    manifest = json.loads((output / "manifest.json").read_text())
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
        (output / manifest["runs"][0]["report"]).write_text("{}")
    elif fault == "reference":
        (output / "reference.json").write_text("{}")
    else:
        manifest["runs"][0]["command"].append("--cov=nova")
    (output / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        analyzer.analyze(output)


def test_cli_analysis_and_no_overwrite(profiles, tmp_path, capsys):
    output = tmp_path / "analysis.json"
    analyzer.main([str(profiles), "--json", str(output)])
    text = capsys.readouterr().out
    assert "lifecycle_mean=" in text and "gen0/1/2=" in text
    assert json.loads(output.read_text())["complete"]
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        analyzer.main([str(profiles), "--json", str(output)])
    assert output.read_bytes() == before
    with pytest.raises(FileExistsError):
        runner.main(["--reference-dir", "unused", "--output-dir", str(profiles)])


def test_child_failure_preserves_old_sources_and_incomplete_evidence(
    profiles, tmp_path, monkeypatch
):
    _, reference, _, _ = load(profiles)
    before = {
        p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in reference["source_sha256"]
    }
    monkeypatch.setattr(
        runner, "prepare_reference", lambda path: (reference, profiles / "payloads")
    )
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=9))
    output = tmp_path / "failed"
    with pytest.raises(RuntimeError, match="Worker failed"):
        runner.main(["--reference-dir", "unused", "--output-dir", str(output)])
    manifest = json.loads((output / "manifest.json").read_text())
    assert not manifest["complete"] and manifest["runs"][0]["returncode"] == 9
    assert before == {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in before}

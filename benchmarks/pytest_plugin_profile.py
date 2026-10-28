"""Pytest plugin profiles sharing the previous serializer timing function."""

import hashlib
import json
import os
import sys
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
    "benchmarks/pytest_plugin_profile.py",
    "benchmarks/pytest_plugin_profile_test.py",
    "scripts/run_pytest_plugin_profile.py",
    "scripts/summarize_pytest_plugin_profile.py",
    "pytest.plugin-profile.ini",
)


def plugin_evidence(config):
    manager = config.pluginmanager
    registrations = []
    for name, plugin in manager.list_name_plugin():
        if plugin is None:
            continue
        if isinstance(plugin, types.ModuleType):
            module = plugin.__name__
        elif isinstance(plugin, type):
            module = plugin.__module__
        else:
            module = type(plugin).__module__
        registrations.append([name, module])
    return {
        "autoload_disabled": os.getenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD") == "1",
        "distributions": sorted(
            [dist.project_name, dist.version] for _, dist in manager.list_plugin_distinfo()
        ),
        "registered_plugins": sorted(registrations),
        "arguments": list(config.invocation_params.args),
        "config_file": str(Path(config.inipath).relative_to(common.ROOT)),
        "root_is_project": config.rootpath == common.ROOT,
        "asyncio_mode": config.getini("asyncio_mode") if manager.hasplugin("asyncio") else None,
    }


def check_plugins(evidence, job):
    profile = job["profile"]
    assert profile in ("autoload", "django_only")
    assert evidence["autoload_disabled"] == (profile == "django_only")
    assert evidence["distributions"] == job["expected_plugins"], "Unexpected plugin distributions"
    assert evidence["config_file"] == "pytest.plugin-profile.ini" and evidence["root_is_project"]
    modules = {module for _, module in evidence["registered_plugins"]}
    assert "pytest_django.plugin" in modules, "pytest-django must be explicitly available"
    if profile == "django_only":
        assert all(
            module.startswith("_pytest.") or module == "pytest_django.plugin" for module in modules
        ), "Unexpected registered plugin in django-only worker"
        assert evidence["asyncio_mode"] is None
    elif any(name == "pytest-asyncio" for name, _ in evidence["distributions"]):
        assert evidence["asyncio_mode"] == "auto"


def execute(pytest_config):
    from nova.cache.backends.serializers import PickleSerializer

    job = json.loads(Path(os.environ["NOVA_PLUGIN_JOB"]).read_text())
    assert job["worker"] == "pytest" and job["mode"] in ("timing", "startup", "after_warmup")
    output = Path(os.environ["NOVA_PLUGIN_OUTPUT"])
    assert not output.exists(), "Report already exists"
    directory = Path(os.environ["NOVA_ENV_PAYLOAD_DIR"])
    raw_bundle = (directory / "payloads.json").read_bytes()
    bundle = json.loads(raw_bundle)
    # Inventory support is not imported into timing workers. Both profiles of
    # an inventory cell import it before their startup checkpoint.
    if job["mode"] != "timing":
        from benchmarks import gc_inventory as census
    else:
        assert "benchmarks.gc_inventory" not in sys.modules
    report = {
        "schema_version": 1,
        "experiment": "pytest_plugin_profile_worker",
        "complete": False,
        "configuration": job,
        "process_id": os.getpid(),
        "started_utc": datetime.now(UTC).isoformat(),
        "source_sha256": {
            path: hashlib.sha256((common.ROOT / path).read_bytes()).hexdigest()
            for path in SOURCE_FILES
        },
        "reference_sha256": os.environ["NOVA_PLUGIN_REFERENCE_SHA256"],
        "payload_bundle_sha256": hashlib.sha256(raw_bundle).hexdigest(),
    }
    try:
        before = plugin_evidence(pytest_config)
        report["plugins"] = before["distributions"]
        report["profile_evidence"] = {"before": before}
        check_plugins(before, job)
        common.clean_runtime("pytest", bundle["gc_policy"])
        report["environment"] = common.metadata()
        report["runtime_before"] = {
            "pytest_modules": common.pytest_modules(),
            "module_count": len(sys.modules),
        }
        serializer = PickleSerializer()
        preparation = warmed = None
        active = job["mode"] != "startup"
        if active:
            with common.no_sql(), ArmDiagnostics(record_phases=False) as preparation:
                payload, expected, bundle, digest = common.load_input(directory, job, serializer)
            assert digest == report["payload_bundle_sha256"]
            report["payload_sha256"] = hashlib.sha256(payload).hexdigest()
        if job["mode"] == "timing":
            report["hint_control"] = bundle["hint_control"]
            report.update(common.timed_reads(serializer, payload, expected, job))
            report["preparation_gc"] = common.scope_report(preparation)
            assert "benchmarks.gc_inventory" not in sys.modules
        else:
            if active:
                warmed = census.warmup(serializer, payload, expected, job)
            with common.no_sql():
                report["inventory"] = census.take_inventory()
            report["prefix_gc"] = {
                name: common.scope_report(probe)
                for name, probe in (("preparation", preparation), ("warmup", warmed))
                if probe is not None
            }
            report["work"] = {
                "preflight_loads": int(active),
                "warmup_loads": 1 + job["warmups"] if active else 0,
                "warmup_validation_calls": 1 + (job["warmups"] if job["validate"] else 0)
                if active
                else 0,
                "timed_operations": 0,
                "sql_count": 0,
                "snapshots": 1,
            }
        common.clean_runtime("pytest", bundle["gc_policy"])
        after = plugin_evidence(pytest_config)
        check_plugins(after, job)
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

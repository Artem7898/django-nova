"""Validate paired plugin profiles; compare timings separately from inventories."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import summarize_gc_inventory as census  # noqa: E402
from scripts import summarize_serializer_environment as timing  # noqa: E402
from scripts.run_pytest_plugin_profile import command_for, expected_plugins, jobs_for  # noqa: E402
from scripts.summarize_serializer_gc import require, summarize_scope  # noqa: E402


def validate_report(report, job, reference, bundle, digest, sources=None):
    require(
        report.get("complete") is True
        and report.get("schema_version") == 1
        and report.get("experiment") == "pytest_plugin_profile_worker",
        "Incomplete/wrong worker",
    )
    require(report["configuration"] == job and job["worker"] == "pytest", "Wrong job")
    require(
        job["expected_plugins"] == expected_plugins(reference, job["profile"]),
        "Wrong plugin expectation",
    )
    require(report["reference_sha256"] == digest, "Reference mismatch")
    require(report["environment"] == reference["environment"], "Previous environment differs")
    require(
        all(report["source_sha256"].get(k) == v for k, v in reference["source_sha256"].items()),
        "Previous source changed",
    )
    require(
        sources is None or report["source_sha256"] == sources, "Sources changed between workers"
    )
    evidence = report["profile_evidence"]
    require(evidence["before"] == evidence["after"], "Plugin evidence changed during worker")
    for data in evidence.values():
        require(
            data["distributions"] == report["plugins"] == job["expected_plugins"],
            "Unexpected active distributions",
        )
        require(
            data["autoload_disabled"] is (job["profile"] == "django_only"), "Wrong autoload policy"
        )
        require(data["arguments"] == command_for(job["profile"])[3:], "Unexpected pytest arguments")
        require(
            data["config_file"] == "pytest.plugin-profile.ini" and data["root_is_project"] is True,
            "Wrong pytest configuration/root",
        )
        registrations = data["registered_plugins"]
        require(
            registrations == sorted(registrations)
            and len({name for name, _ in registrations}) == len(registrations),
            "Duplicate or unsorted registrations",
        )
        modules = {module for _, module in registrations}
        require("pytest_django.plugin" in modules, "Django plugin missing")
        if job["profile"] == "django_only":
            require(
                all(
                    module.startswith("_pytest.") or module == "pytest_django.plugin"
                    for module in modules
                ),
                "Unexpected registered django-only plugin",
            )
            require(data["asyncio_mode"] is None, "Async plugin unexpectedly active")
        elif any(name == "pytest-asyncio" for name, _ in report["plugins"]):
            require(data["asyncio_mode"] == "auto", "Full profile lost asyncio configuration")
    require(
        all(job[k] == reference["workload"][k] for k in ("rows", "warmups", "operations")),
        "Unequal workload",
    )
    require(
        job["operation"] == "loads" and job["scenario"] == "prefetch_related", "Wrong operation"
    )
    require(job["mode"] in ("timing", "startup", "after_warmup"), "Unknown worker mode")
    if job["mode"] == "timing":
        require(
            job["checkpoint"] is None and "inventory" not in report and "prefix_gc" not in report,
            "Inventory mixed into timing worker",
        )
        require(
            report["runtime_after"]["census_imported"] is False, "Timing worker imported census"
        )
        timing.validate_report(
            {**report, "experiment": "serializer_environment_worker"},
            job,
            bundle,
            reference["payload_bundle_sha256"],
        )
    else:
        require(
            job["checkpoint"] == job["mode"] and "operations" not in report and "gc" not in report,
            "Timing mixed into inventory worker",
        )
        require(
            report["runtime_after"]["census_imported"] is True, "Census worker missing inspector"
        )
        census.validate_report(
            {**report, "experiment": "gc_inventory_worker"},
            job,
            {**reference, "plugins": job["expected_plugins"]},
            bundle,
            digest,
            sources,
        )
    return report["source_sha256"]


def ratio(numerator, denominator):
    return numerator / denominator if numerator is not None and denominator else None


def analyze(directory, slow_ms=20):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    require(
        manifest.get("complete") is True
        and manifest.get("schema_version") == 1
        and manifest.get("experiment") == "pytest_plugin_profile_matrix",
        "Incomplete/wrong matrix",
    )
    raw = (directory / "reference.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    require(digest == manifest["reference_sha256"], "Reference modified")
    reference = json.loads(raw)
    raw_bundle = (directory / "payloads/payloads.json").read_bytes()
    require(
        hashlib.sha256(raw_bundle).hexdigest() == reference["payload_bundle_sha256"],
        "Payload bundle modified",
    )
    bundle = json.loads(raw_bundle)
    timing.check_bundle(bundle)
    for item in bundle["payloads"].values():
        data = timing.within(directory / "payloads", item["file"]).read_bytes()
        require(
            len(data) == item["bytes"] and hashlib.sha256(data).hexdigest() == item["sha256"],
            "Payload modified",
        )
    plan, runs = manifest["plan"], manifest["runs"]
    require(
        plan == jobs_for(reference, manifest["repeats"], manifest["seed"]), "Matrix plan differs"
    )
    require(
        len(plan) == len(runs) == manifest["expected_workers"] == manifest["repeats"] * 18,
        "Wrong matrix size",
    )
    seen, pids, pairs, snapshots = set(), set(), [], {}
    sources, previous_end = None, 0
    for offset in range(0, len(runs), 2):
        first = plan[offset]
        pair = {
            "configuration": {
                k: v for k, v in first.items() if k not in ("profile", "expected_plugins")
            },
            "workers": {},
        }
        inventories = {}
        for job, entry in zip(plan[offset : offset + 2], runs[offset : offset + 2], strict=True):
            require(
                entry["configuration"] == job and entry["returncode"] == 0,
                "Failed/mismatched worker",
            )
            require(
                entry["command"][1:] == command_for(job["profile"])[1:],
                "Recorded worker command differs",
            )
            require(
                previous_end <= entry["launched_ns"] < entry["finished_ns"], "Workers overlapped"
            )
            previous_end = entry["finished_ns"]
            path = timing.within(directory, entry["report"])
            require(path not in seen, "Duplicate report")
            seen.add(path)
            raw_report = path.read_bytes()
            require(
                hashlib.sha256(raw_report).hexdigest() == entry["report_sha256"], "Report modified"
            )
            report = json.loads(raw_report)
            require(
                report["process_id"] == entry["process_id"] and report["process_id"] not in pids,
                "Repeated/wrong process",
            )
            pids.add(report["process_id"])
            sources = validate_report(report, job, reference, bundle, digest, sources)
            profile = job["profile"]
            info = {"report": entry["report"], "plugins": report["plugins"]}
            if job["mode"] == "timing":
                info.update(timing.metrics(report, slow_ms))
            else:
                inv = report["inventory"]
                inventories[profile] = inv
                snapshots[
                    (job["mode"], job["hint_variant"], job["validate"], job["repeat"], profile)
                ] = report
                info.update(
                    generation_counts=inv["generation_counts"],
                    total=inv["total"],
                    frozen_objects_excluded=inv["inspection"]["frozen_objects_before"],
                    prefix_gc={
                        name: summarize_scope(scope) for name, scope in report["prefix_gc"].items()
                    },
                )
            pair["workers"][profile] = info
        if first["mode"] == "timing":
            full, lean = (pair["workers"][name] for name in ("autoload", "django_only"))
            pair["django_only_over_autoload"] = {
                "empty_gen2_mean": ratio(
                    lean["gen2"]["loop"]["collected_zero"]["mean_ms"],
                    full["gen2"]["loop"]["collected_zero"]["mean_ms"],
                ),
                "lifecycle_mean": ratio(lean["lifecycle"]["mean_ms"], full["lifecycle"]["mean_ms"]),
            }
        else:
            pair["autoload_minus_django_only"] = census.difference(
                inventories["django_only"], inventories["autoload"]
            )
        pairs.append(pair)
    deltas = []
    for (mode, hint, val, repeat, profile), report in snapshots.items():
        if mode == "after_warmup":
            start = snapshots[("startup", None, None, repeat, profile)]
            deltas.append(
                {
                    "hint_variant": hint,
                    "validate": val,
                    "repeat": repeat,
                    "profile": profile,
                    "startup_process_id": start["process_id"],
                    "warmup_process_id": report["process_id"],
                    "after_warmup_minus_startup": census.difference(
                        start["inventory"], report["inventory"]
                    ),
                }
            )
    return {
        "schema_version": 1,
        "experiment": "pytest_plugin_profile_analysis",
        "complete": True,
        "slow_ms": slow_ms,
        "reference": reference,
        "pairs": pairs,
        "warmup_deltas": deltas,
        "interpretation": "Timing and inventory use different processes. Counts do not measure retained bytes or explain pause cost by themselves. Profiles differ as a group; this does not identify an individual plugin. Compare within adjacent new pairs, not against old latencies.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--slow-ms", type=float, default=20)
    args = parser.parse_args(argv)
    require(args.slow_ms > 0, "slow-ms must be positive")
    result = analyze(args.directory, args.slow_ms)
    if args.json:
        with args.json.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
    for pair in result["pairs"]:
        c = pair["configuration"]
        print(f"{c['pair_id']} {c['mode']} {c['hint_variant']} validation={c['validate']}")
        for profile in c["pair_order"]:
            data = pair["workers"][profile]
            if c["mode"] == "timing":
                zero = data["gen2"]["loop"]["collected_zero"]
                mean = "n/a" if zero["mean_ms"] is None else f"{zero['mean_ms']:.3f}ms"
                print(
                    f"  {profile:11} lifecycle_mean={data['lifecycle']['mean_ms']:.3f}ms "
                    f"empty_gen2={zero['count']} empty_gen2_mean={mean} "
                    f"loads_p99={data['call_p99_ms']:.3f}ms slow={data['slow']['count']}"
                )
            else:
                print(
                    f"  {profile:11} gen0/1/2={data['generation_counts']} "
                    f"total={data['total']} frozen_excluded={data['frozen_objects_excluded']}"
                )
    print("Full GC scopes, per-pair ratios and type/module deltas are in JSON.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        print(f"Plugin profile analysis failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error

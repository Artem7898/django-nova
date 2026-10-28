"""Offline comparison of paired Django/pytest workers; never unpickles inputs."""

import argparse
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.summarize_serializer_gc import analyze_timing, require, summarize_scope  # noqa: E402


def check_bundle(bundle):
    require(
        bundle.get("complete") is True
        and bundle.get("schema_version") == 1
        and bundle.get("experiment") == "serializer_environment_payloads",
        "Invalid payload bundle",
    )
    proof, rows = bundle["hint_control"], bundle["rows"]
    require(
        rows > 0
        and proof["removed_instance_hints"] == proof["articles"] == rows
        and proof["tag_instances"] == rows * 3,
        "Wrong payload model counts",
    )
    require(
        all(
            proof[key] is True
            for key in (
                "independent_graphs",
                "original_unchanged",
                "fields_and_loaded_tags_equal",
                "roundtrip_equal_without_sql",
            )
        ),
        "Invalid semantic proof",
    )
    require(set(bundle["payloads"]) == {"original", "without_instance"}, "Missing payload variant")
    require(len(bundle["expected"]) == rows, "Wrong validation row count")
    for variant, item in bundle["payloads"].items():
        require(
            item["bytes"] == proof["payload_bytes"][variant] and len(item["sha256"]) == 64,
            "Invalid payload metadata",
        )
    policy = bundle["gc_policy"]
    require(
        policy["enabled"] is True and policy["thresholds"][0] > 0 and policy["debug"] == 0,
        "Expected normal enabled GC without debug flags",
    )


def check_reference(previous, bundle, jobs):
    require(
        previous.get("complete") is True and previous.get("experiment") == "serializer_gc_matrix",
        "Previous manifest is incomplete or incompatible",
    )
    require(len(previous["runs"]) == previous["expected_workers"], "Previous matrix is incomplete")
    env = previous["reference"]["environment"]
    for key in ("python", "django", "versions", "database_vendor", "database_version"):
        require(env[key] == bundle["environment"][key], f"Previous environment differs: {key}")
    require(
        env["cache_source_sha256"]["backends/serializers.py"]
        == bundle["environment"]["cache_source_sha256"]["backends/serializers.py"],
        "Production serializer differs from previous experiment",
    )
    require(
        env["gc_thresholds"] == bundle["gc_policy"]["thresholds"]
        and env["gc_enabled"] == bundle["gc_policy"]["enabled"]
        and env["gc_debug"] == bundle["gc_policy"]["debug"],
        "Previous GC policy differs",
    )
    for variant in bundle["payloads"]:
        found = [
            run
            for run in previous["runs"]
            if run["configuration"].get("hint_variant") == variant
            and run["configuration"]["mode"] == "timing"
        ]
        require(found, f"Previous matrix has no timing payload: {variant}")
        for run in found:
            require(
                run["returncode"] == 0
                and run["payload_sha256"] == bundle["payloads"][variant]["sha256"]
                and run["payload_bytes"] == bundle["payloads"][variant]["bytes"],
                "Previous payload bytes differ",
            )
            for key in ("rows", "operations", "warmups", "operation", "scenario"):
                require(
                    run["configuration"][key] == jobs[0][key],
                    f"Previous operation settings differ: {key}",
                )


def validate_report(report, config, bundle, digest, reference=None):
    require(
        report.get("complete") is True
        and report.get("schema_version") == 1
        and report.get("experiment") == "serializer_environment_worker",
        "Incomplete/wrong worker report",
    )
    require(
        report["configuration"] == config and config["rows"] == bundle["rows"],
        "Worker configuration mismatch",
    )
    require(
        report["payload_bundle_sha256"] == digest
        and report["payload_sha256"] == bundle["payloads"][config["hint_variant"]]["sha256"],
        "Worker payload mismatch",
    )
    require(report["hint_control"] == bundle["hint_control"], "Worker semantic proof differs")
    require(report["summary"]["sql_count"] == 0, "Worker executed SQL")
    require(
        report["summary"]["validation_calls"]
        == (config["operations"] if config["validate"] else 0),
        "Validation count mismatch",
    )
    environment = report["environment"]
    require(environment["gc_policy"] == bundle["gc_policy"], "Worker GC policy differs")
    for key in ("python", "django"):
        require(environment[key] == bundle["environment"][key], f"Worker version mismatch: {key}")
    require(environment["pythonhashseed"] == "0", "Hash seed changed")
    for side in ("runtime_before", "runtime_after"):
        modules = report[side]["pytest_modules"]
        if config["worker"] == "minimal":
            require(not modules and not report["plugins"], "Minimal worker imported pytest/plugins")
        else:
            require(
                config["worker"] == "pytest" and "pytest" in modules and report["plugins"],
                "Pytest plugins are absent",
            )
    current = {"environment": environment, "source_sha256": report["source_sha256"]}
    require(reference is None or current == reference, "Worker environment or source changed")
    previous_end = 0
    for name in ("preparation_gc", "warmup_gc", "gc"):
        scope = report[name]
        require(previous_end <= scope["start_ns"], "GC scopes overlap or are out of order")
        previous_end = scope["end_ns"]
        for side in ("before", "after"):
            require(
                scope[side]["enabled"] == bundle["gc_policy"]["enabled"]
                and scope[side]["thresholds"] == bundle["gc_policy"]["thresholds"],
                "GC settings changed inside scope",
            )
        for gen in summarize_scope(scope):
            before, after = (
                scope["before"]["stats"][gen["generation"]],
                scope["after"]["stats"][gen["generation"]],
            )
            for field in ("collections", "collected", "uncollectable"):
                require(
                    after[field] - before[field] == gen[field],
                    "Callback events disagree with gc.get_stats()",
                )
    analyze_timing(report, 20)
    return current


def event_summary(events):
    durations = sorted((event["end_ns"] - event["start_ns"]) / 1e6 for event in events)
    return {
        "count": len(durations),
        "total_ms": sum(durations),
        "mean_ms": statistics.fmean(durations) if durations else None,
        "median_ms": statistics.median(durations) if durations else None,
        "p95_ms": durations[math.ceil(0.95 * len(durations)) - 1] if durations else None,
        "max_ms": durations[-1] if durations else None,
        "collected": sum(e["collected"] for e in events),
        "uncollectable": sum(e["uncollectable"] for e in events),
    }


def metrics(report, slow_ms):
    result = analyze_timing(report, slow_ms)
    result["gen2"] = {}
    for scope, key in (("preparation", "preparation_gc"), ("warmup", "warmup_gc"), ("loop", "gc")):
        events = [event for event in report[key]["events"] if event["generation"] == 2]
        result["gen2"][scope] = {
            "all": event_summary(events),
            "collected_zero": event_summary([e for e in events if e["collected"] == 0]),
            "collected_nonzero": event_summary([e for e in events if e["collected"] != 0]),
        }
    return result


def within(directory, filename):
    path = (directory / filename).resolve()
    require(path.is_relative_to(directory.resolve()), "Report path escapes matrix")
    return path


def analyze(directory, slow_ms=20):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    require(
        manifest.get("complete") is True
        and manifest.get("schema_version") == 1
        and manifest.get("experiment") == "serializer_environment",
        "Incomplete/wrong matrix",
    )
    runs, plan = manifest["runs"], manifest["plan"]
    require(
        len(runs) == len(plan) == manifest["expected_workers"] == manifest["repeats"] * 8,
        "Matrix worker count mismatch",
    )
    require(manifest["producer"]["returncode"] == 0, "Payload producer failed")
    payload_dir = within(directory, manifest["payload_directory"])
    raw = (payload_dir / "payloads.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    require(digest == manifest["payload_bundle_sha256"], "Payload bundle was modified")
    bundle = json.loads(raw)
    check_bundle(bundle)
    for item in bundle["payloads"].values():
        payload = within(payload_dir, item["file"]).read_bytes()
        require(
            len(payload) == item["bytes"] and hashlib.sha256(payload).hexdigest() == item["sha256"],
            "Payload file was modified",
        )
    reference = None
    plugins = None
    seen, pids, cells, pairs = set(), set(), set(), []
    previous_end = 0
    for offset in range(0, len(runs), 2):
        first, second = plan[offset : offset + 2]
        require(
            {**first, "worker": second["worker"]} == second and first["worker"] != second["worker"],
            "Pair configurations differ",
        )
        order = ["minimal", "pytest"] if first["repeat"] % 2 else ["pytest", "minimal"]
        require(
            first["pair_order"] == order == [first["worker"], second["worker"]],
            "Pair order is not alternating",
        )
        require(
            first["mode"] == "timing"
            and first["operation"] == "loads"
            and first["scenario"] == "prefetch_related",
            "Wrong experiment operation",
        )
        require(
            all(first[key] == plan[0][key] for key in ("rows", "operations", "warmups")),
            "Unequal work across matrix cells",
        )
        cell = (first["hint_variant"], first["validate"], first["repeat"])
        require(cell not in cells, "Duplicate matrix cell")
        cells.add(cell)
        pair = {
            "pair_id": first["pair_id"],
            "hint_variant": first["hint_variant"],
            "validate": first["validate"],
            "repeat": first["repeat"],
            "order": order,
            "workers": {},
        }
        for config, entry in zip((first, second), runs[offset : offset + 2], strict=True):
            require(
                entry["configuration"] == config and entry["returncode"] == 0,
                "Failed/unexpected worker",
            )
            require(
                previous_end <= entry["launched_ns"] < entry["finished_ns"],
                "Workers were not sequential",
            )
            previous_end = entry["finished_ns"]
            path = within(directory, entry["report"])
            require(path not in seen, "Duplicate worker report")
            seen.add(path)
            data = path.read_bytes()
            require(
                hashlib.sha256(data).hexdigest() == entry["report_sha256"],
                "Worker report was modified",
            )
            report = json.loads(data)
            require(
                report["process_id"] == entry["process_id"] and report["process_id"] not in pids,
                "Worker process identity repeated/mismatched",
            )
            pids.add(report["process_id"])
            reference = validate_report(report, config, bundle, digest, reference)
            require(
                report["environment"]["base_settings"] == manifest["settings"],
                "Django settings mismatch",
            )
            if config["worker"] == "pytest":
                require(plugins is None or plugins == report["plugins"], "Plugin inventory changed")
                plugins = report["plugins"]
            pair["workers"][config["worker"]] = {
                "report": entry["report"],
                **metrics(report, slow_ms),
            }
        minimal = pair["workers"]["minimal"]["gen2"]["loop"]["collected_zero"]
        pytest = pair["workers"]["pytest"]["gen2"]["loop"]["collected_zero"]
        pair["zero_gen2_mean_ratio_pytest_over_minimal"] = (
            pytest["mean_ms"] / minimal["mean_ms"]
            if minimal["mean_ms"] and pytest["mean_ms"] is not None
            else None
        )
        pairs.append(pair)
    expected = {
        (h, v, r)
        for h in ("original", "without_instance")
        for v in (False, True)
        for r in range(1, manifest["repeats"] + 1)
    }
    require(cells == expected, "Missing or unexpected matrix cells")
    return {
        "schema_version": 1,
        "experiment": "serializer_environment_analysis",
        "complete": True,
        "slow_ms": slow_ms,
        "pairs": pairs,
        "environment": reference,
        "plugins": plugins,
        "interpretation": "Ratios are per adjacent process pair. Empty GC buckets have no latency estimate. Do not pool hint/validation cells or infer significance from four repeats.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--slow-ms", type=float, default=20)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    require(math.isfinite(args.slow_ms) and args.slow_ms > 0, "slow-ms must be finite and positive")
    result = analyze(args.directory, args.slow_ms)
    if args.json:
        with args.json.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
    for pair in result["pairs"]:
        for worker in pair["order"]:
            item = pair["workers"][worker]
            zero = item["gen2"]["loop"]["collected_zero"]
            mean = "n/a" if zero["mean_ms"] is None else f"{zero['mean_ms']:.3f}ms"
            gens = item["scopes"]["loop"]
            print(
                f"{pair['pair_id']:6} {pair['hint_variant']:16} validation={int(pair['validate'])} {worker:7} loads={item['call_mean_ms']:.3f}ms full={item['lifecycle']['mean_ms']:.3f}ms gen2_zero_n={zero['count']} gen2_zero_mean={mean} gc_counts={[g['collections'] for g in gens]} collected={[g['collected'] for g in gens]}"
            )
    print(
        "All scopes and per-pair comparisons are in JSON. n/a means no observed collections; not zero cost."
    )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        print(f"Analysis failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error

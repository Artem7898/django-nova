"""Validate a preparation-GC matrix; compare automatic counts, positions and latency."""

import argparse
import hashlib
import json
import math
import statistics
import sys
from bisect import bisect_right
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import summarize_serializer_environment as timing  # noqa: E402
from scripts.run_gc_preparation import command_for, jobs_for  # noqa: E402
from scripts.run_pytest_plugin_profile import expected_plugins  # noqa: E402
from scripts.summarize_serializer_environment import check_bundle, within  # noqa: E402
from scripts.summarize_serializer_gc import require, summarize_scope  # noqa: E402


def validate_report(report, job, reference, bundle, digest, sources=None):
    require(
        report.get("complete") is True
        and report.get("schema_version") == 1
        and report.get("experiment") == "gc_preparation_worker",
        "Incomplete/wrong worker",
    )
    require(report["configuration"] == job, "Wrong job")
    require(
        job["worker"] == "pytest"
        and job["operation"] == "loads"
        and job["mode"] == "timing"
        and job["checkpoint"] is None
        and type(job["prepare_gc"]) is bool
        and job["scenario"] == "prefetch_related"
        and job["hint_variant"] == "without_instance"
        and type(job["validate"]) is bool
        and all(job[k] == reference["workload"][k] for k in ("rows", "warmups", "operations")),
        "Workload differs",
    )
    require(
        report["reference_sha256"] == digest
        and report["payload_bundle_sha256"] == reference["payload_bundle_sha256"]
        and report["payload_sha256"] == bundle["payloads"]["without_instance"]["sha256"]
        and report["hint_control"] == bundle["hint_control"],
        "Payload/reference differs",
    )
    require(report["environment"] == reference["environment"], "Environment differs")
    require(
        all(report["source_sha256"].get(k) == v for k, v in reference["source_sha256"].items())
        and (sources is None or report["source_sha256"] == sources),
        "Previous/current source changed",
    )
    evidence = report["profile_evidence"]
    require(
        set(evidence) == {"before", "after"} and evidence["before"] == evidence["after"],
        "Plugin drift",
    )
    data = evidence["before"]
    require(
        data["distributions"]
        == report["plugins"]
        == job["expected_plugins"]
        == expected_plugins(reference, job["profile"]),
        "Unexpected plugin distributions",
    )
    require(
        data["autoload_disabled"] is (job["profile"] == "django_only")
        and data["arguments"] == command_for(job["profile"])[3:]
        and data["root_is_project"] is True
        and data["config_file"] == "pytest.plugin-profile.ini",
        "Wrong pytest invocation",
    )
    registered = data["registered_plugins"]
    require(
        registered == sorted(registered) and len({x[0] for x in registered}) == len(registered),
        "Invalid registrations",
    )
    modules = {module for _, module in registered}
    require("pytest_django.plugin" in modules, "Django plugin missing")
    if job["profile"] == "django_only":
        require(
            all(m.startswith("_pytest.") or m == "pytest_django.plugin" for m in modules),
            "Unlisted plugin",
        )
        require(data["asyncio_mode"] is None, "Unexpected asyncio plugin")
    elif any(name == "pytest-asyncio" for name, _ in report["plugins"]):
        require(data["asyncio_mode"] == "auto", "Wrong asyncio configuration")
    require(
        "inventory" not in report and report["runtime_after"]["census_imported"] is False,
        "Inventory mixed with timing",
    )
    guard = report["gc_guard_before"]
    require(
        guard == report["gc_guard_after"]
        and guard["policy"] == bundle["gc_policy"]
        and guard["callbacks"] == 0
        and guard["python_threads"] == 1
        and type(guard["frozen"]) is int
        and guard["frozen"] >= 0,
        "GC policy, callbacks, threads or frozen state changed",
    )
    timing.validate_report(
        {**report, "experiment": "serializer_environment_worker"},
        job,
        bundle,
        reference["payload_bundle_sha256"],
    )
    check_conditioning(report, bundle["gc_policy"])
    return report["source_sha256"]


def check_conditioning(report, policy):
    scope, call = report["conditioning_gc"], report["conditioning_call"]
    require(
        report["warmup_gc"]["end_ns"]
        <= scope["start_ns"]
        <= call["start_ns"]
        <= call["end_ns"]
        <= scope["end_ns"]
        <= report["gc"]["start_ns"],
        "Conditioning overlaps warmup or the measured loop",
    )
    for side in ("before", "after"):
        require(
            scope[side]["enabled"] is True and scope[side]["thresholds"] == policy["thresholds"],
            "GC changed during conditioning",
        )
    for gen in summarize_scope(scope):
        g = gen["generation"]
        require(
            all(
                scope["after"]["stats"][g][k] - scope["before"]["stats"][g][k] == gen[k]
                for k in ("collections", "collected", "uncollectable")
            ),
            "Conditioning callbacks disagree with GC stats",
        )
    requested = report["configuration"]["prepare_gc"]
    require(call["requested"] is requested, "Wrong conditioning request")
    if requested:
        found = [
            e
            for e in scope["events"]
            if call["start_ns"] <= e["start_ns"] and e["end_ns"] <= call["end_ns"]
        ]
        require(
            len(found) == 1 and found[0]["generation"] == 2,
            "Expected one explicit gen-2 collection inside the call",
        )
        require(
            type(call["returned"]) is int
            and call["returned"] >= 0
            and call["returned"] == found[0]["collected"] + found[0]["uncollectable"],
            "Explicit collection return disagrees with callback",
        )
    else:
        require(call["returned"] is None, "Control must not call collect")


def gen2_positions(report):
    """Locate starts, retaining events outside reads instead of assigning fake indices."""
    operations = report["operations"]
    starts = [op["start_ns"] for op in operations]
    positions = []
    for event in report["gc"]["events"]:
        if event["generation"] != 2:
            continue
        t = event["start_ns"]
        i = bisect_right(starts, t) - 1
        phase, index, following = "before_first_read", None, 0
        if i >= 0:
            op = operations[i]
            if t < op["end_ns"]:
                phase, index, following = "loads", i, None
            elif op.get("validation_start_ns", t + 1) <= t < op.get("validation_end_ns", t):
                phase, index, following = "validation", i, None
            elif t < op["released_ns"]:
                phase, index, following = "post_load_or_disposal", i, None
            else:
                following = i + 1 if i + 1 < len(operations) else None
                phase = "between_reads" if following is not None else "after_last_read"
        positions.append(
            {
                "operation_index": index,
                "previous_index": i if index is None and i >= 0 else None,
                "next_index": following,
                "phase": phase,
                "start_from_loop_ms": (t - report["gc"]["start_ns"]) / 1e6,
                "duration_ms": (event["end_ns"] - t) / 1e6,
                "collected": event["collected"],
                "uncollectable": event["uncollectable"],
            }
        )
    return positions


def metrics(report, slow_ms):
    result = timing.metrics(report, slow_ms)
    result["scopes"]["conditioning"] = summarize_scope(report["conditioning_gc"])
    result["gen2_positions"] = gen2_positions(report)
    result["explicit_preparation"] = report["conditioning_call"]
    scope, call = report["conditioning_gc"], report["conditioning_call"]
    result["conditioning_ms"] = (scope["end_ns"] - scope["start_ns"]) / 1e6
    result["explicit_preparation_ms"] = (
        (call["end_ns"] - call["start_ns"]) / 1e6 if call["requested"] else None
    )
    result["loop_gc_ms"] = sum(g["duration_ns"] for g in result["scopes"]["loop"]) / 1e6
    result["loop_gen2_ms"] = result["gen2"]["loop"]["all"]["total_ms"]
    result["loop_gen2_count"] = result["gen2"]["loop"]["all"]["count"]
    result["loop_gen2_per_100_reads"] = result["loop_gen2_count"] * 100 / len(report["operations"])
    return result


def ratio(a, b):
    return a / b if b else None


def contrast(before, after):
    return {
        "gen2_count_delta": after["loop_gen2_count"] - before["loop_gen2_count"],
        "gen2_ms_delta": after["loop_gen2_ms"] - before["loop_gen2_ms"],
        "all_gc_ms_delta": after["loop_gc_ms"] - before["loop_gc_ms"],
        "lifecycle_mean_ms_delta": after["lifecycle"]["mean_ms"] - before["lifecycle"]["mean_ms"],
        "lifecycle_mean_ratio": ratio(
            after["lifecycle"]["mean_ms"], before["lifecycle"]["mean_ms"]
        ),
    }


def distribution(values):
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def analyze(directory, slow_ms=20):
    require(math.isfinite(slow_ms) and slow_ms > 0, "slow-ms must be positive and finite")
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text())
    require(
        manifest.get("complete") is True
        and manifest.get("schema_version") == 1
        and manifest.get("experiment") == "gc_preparation_matrix",
        "Incomplete/wrong matrix",
    )
    raw = (directory / "reference.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    require(digest == manifest["reference_sha256"], "Reference modified")
    reference = json.loads(raw)
    raw = (directory / "payloads/payloads.json").read_bytes()
    require(
        hashlib.sha256(raw).hexdigest() == reference["payload_bundle_sha256"], "Bundle modified"
    )
    bundle = json.loads(raw)
    check_bundle(bundle)
    for item in bundle["payloads"].values():
        raw = within(directory / "payloads", item["file"]).read_bytes()
        require(
            len(raw) == item["bytes"] and hashlib.sha256(raw).hexdigest() == item["sha256"],
            "Payload modified",
        )
    require(type(manifest["repeats"]) is int and manifest["repeats"] > 0, "Invalid repeats")
    plan, runs = manifest["plan"], manifest["runs"]
    require(plan == jobs_for(reference, manifest["repeats"], manifest["seed"]), "Plan differs")
    require(
        len(plan) == len(runs) == manifest["expected_workers"] == manifest["repeats"] * 8,
        "Incomplete matrix",
    )
    sources, previous_end = None, 0
    paths, pids, pairs = set(), set(), []
    for offset in range(0, len(runs), 2):
        first = plan[offset]
        pair = {
            key: first[key]
            for key in (
                "pair_id",
                "block_id",
                "repeat",
                "profile",
                "validate",
                "pair_order",
                "profile_order",
            )
        }
        pair["workers"] = {}
        for job, entry in zip(plan[offset : offset + 2], runs[offset : offset + 2], strict=True):
            require(
                entry["configuration"] == job and entry["returncode"] == 0,
                "Failed/mismatched child",
            )
            require(
                entry["command"][1:] == command_for(job["profile"])[1:], "Worker command differs"
            )
            require(
                previous_end <= entry["launched_ns"] < entry["finished_ns"], "Workers overlapped"
            )
            previous_end = entry["finished_ns"]
            path = within(directory, entry["report"])
            require(path not in paths, "Duplicate report")
            paths.add(path)
            raw = path.read_bytes()
            require(hashlib.sha256(raw).hexdigest() == entry["report_sha256"], "Report modified")
            report = json.loads(raw)
            require(
                report["process_id"] == entry["process_id"] and entry["process_id"] not in pids,
                "Repeated/wrong PID",
            )
            pids.add(entry["process_id"])
            sources = validate_report(report, job, reference, bundle, digest, sources)
            pair["workers"]["prepared" if job["prepare_gc"] else "unprepared"] = {
                "report": entry["report"],
                **metrics(report, slow_ms),
            }
        pair["prepared_minus_unprepared"] = contrast(
            pair["workers"]["unprepared"], pair["workers"]["prepared"]
        )
        pairs.append(pair)
    summaries = []
    for validate in (False, True):
        for profile in ("autoload", "django_only"):
            selected = [p for p in pairs if p["validate"] == validate and p["profile"] == profile]
            summaries.append(
                {
                    "validate": validate,
                    "profile": profile,
                    "paired_differences": {
                        key: distribution([p["prepared_minus_unprepared"][key] for p in selected])
                        for key in (
                            "gen2_count_delta",
                            "gen2_ms_delta",
                            "all_gc_ms_delta",
                            "lifecycle_mean_ms_delta",
                        )
                    },
                }
            )
    comparisons = []
    for offset in range(0, len(pairs), 2):
        by_profile = {p["profile"]: p for p in pairs[offset : offset + 2]}
        for preparation in ("unprepared", "prepared"):
            a, b = (by_profile[p]["workers"][preparation] for p in ("autoload", "django_only"))
            comparisons.append(
                {
                    "block_id": pairs[offset]["block_id"],
                    "validate": pairs[offset]["validate"],
                    "preparation": preparation,
                    "django_only_minus_autoload": contrast(a, b),
                }
            )
    return {
        "schema_version": 1,
        "experiment": "gc_preparation_analysis",
        "complete": True,
        "reference": reference,
        "slow_ms": slow_ms,
        "pairs": pairs,
        "summaries": summaries,
        "profile_comparisons": comparisons,
        "interpretation": "Primary pairs compare no/one explicit collection within a profile. Loop GC is automatic. Conditioning is outside the loop; its cost is excluded, not eliminated. Observer events and state snapshots affect both arms. Four-repeat summaries are descriptive; compare within this harness, not as an identical heap to previous runs.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--slow-ms", type=float, default=20)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    result = analyze(args.directory, args.slow_ms)
    if args.json:
        with args.json.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
    for pair in result["pairs"]:
        print(
            f"{pair['pair_id']} validation={pair['validate']} preparation_order={pair['pair_order']}"
        )
        for prepare in pair["pair_order"]:
            name = "prepared" if prepare else "unprepared"
            w = pair["workers"][name]
            call = w["explicit_preparation"]
            prep_ms = w["explicit_preparation_ms"]
            prep_text = "n/a" if prep_ms is None else f"{prep_ms:.3f}"
            print(
                f"  {name:10} gen2={w['loop_gen2_count']} gen2_ms={w['loop_gen2_ms']:.3f} all_gc_ms={w['loop_gc_ms']:.3f} full_mean={w['lifecycle']['mean_ms']:.3f}ms full_p99={w['lifecycle']['p99_ms']:.3f}ms slow={w['lifecycle']['slow_count']} prep_ms={prep_text} prep_return={call['returned']}"
            )
            positions = [
                f"{e['operation_index'] if e['operation_index'] is not None else str(e['previous_index']) + '->' + str(e['next_index'])}:{e['phase']}"
                for e in w["gen2_positions"]
            ]
            print("    gen2_positions=" + ",".join(positions))
        print("  prepared-minus-unprepared=" + json.dumps(pair["prepared_minus_unprepared"]))
    print(
        "Loop positions are zero-based. Explicit preparation time is excluded from read latency; GC time overlaps latency and must not be added to it."
    )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        print(f"GC preparation analysis failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error

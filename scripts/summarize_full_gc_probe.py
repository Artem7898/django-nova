"""Validate full-GC probes offline; separate empty from nonempty measurements."""

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_full_gc_probe import command_for, jobs_for  # noqa: E402
from scripts.run_pytest_plugin_profile import expected_plugins  # noqa: E402
from scripts.summarize_serializer_environment import check_bundle, within  # noqa: E402
from scripts.summarize_serializer_gc import require, summarize_scope  # noqa: E402


def state_delta(before, after):
    return [
        {key: b[key] - a[key] for key in ("collections", "collected", "uncollectable")}
        for a, b in zip(before["stats"], after["stats"], strict=True)
    ]


def validate_probe(probe, policy):
    require(
        probe["automatic_gc_suspended_during_pair"] is True
        and probe["explicit_collections"] == 2
        and probe["measured_collections"] == probe["python_threads"] == 1,
        "Wrong collection protocol",
    )
    states = probe["states"]
    names = ("before", "before_preparation", "before_measured", "after_measured", "restored")
    require(set(states) == set(names), "Missing/extra GC checkpoint")
    frozen = states["before"]["frozen_objects"]
    for name in names:
        state = states[name]
        wanted = {**policy, "enabled": name in ("before", "restored")}
        require(state["policy"] == wanted, "GC policy changed or was not restored")
        require(
            state["frozen_objects"] == frozen and state["callback_count"] == 0,
            "Frozen set changed or callbacks were installed",
        )
        require(len(state["counts"]) == len(state["stats"]) == 3, "Wrong GC state size")
        values = [frozen, *state["counts"]]
        for stats in state["stats"]:
            require(set(stats) == {"collections", "collected", "uncollectable"}, "Wrong GC stats")
            values.extend(stats.values())
        require(all(type(n) is int and n >= 0 for n in values), "Invalid GC counter")
    for left, right, return_key in (
        ("before_preparation", "before_measured", "preparation_return"),
        ("before_measured", "after_measured", "measured_return"),
    ):
        delta = state_delta(states[left], states[right])
        require(
            [d["collections"] for d in delta] == [0, 0, 1]
            and all(d[k] >= 0 for d in delta for k in d),
            "Expected exactly one full collection",
        )
        require(
            all(delta[g][k] == 0 for g in (0, 1) for k in delta[g]),
            "Unexpected young-generation collection",
        )
        require(
            type(probe[return_key]) is int
            and probe[return_key] == delta[2]["collected"] + delta[2]["uncollectable"],
            "collect return disagrees with GC stats",
        )
    require(
        states["before"]["stats"] == states["before_preparation"]["stats"]
        and states["after_measured"]["stats"] == states["restored"]["stats"],
        "Extra collection at probe boundary",
    )
    start, end = probe["start_ns"], probe["end_ns"]
    require(
        type(start) is int
        and type(end) is int
        and 0 < start < end
        and probe["elapsed_ns"] == end - start,
        "Invalid measured interval",
    )


def validate_report(report, job, reference, bundle, digest, sources=None):
    require(
        report.get("complete") is True
        and report.get("schema_version") == 1
        and report.get("experiment") == "full_gc_probe_worker",
        "Incomplete/wrong worker",
    )
    require(report["configuration"] == job, "Wrong job")
    require(
        job["worker"] == "pytest"
        and job["operation"] == "full_gc_probe"
        and job["scenario"] == "prefetch_related"
        and job["hint_variant"] == "without_instance"
        and type(job["validate"]) is bool
        and all(job[k] == reference["workload"][k] for k in ("rows", "warmups")),
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
        report["work"]
        == {
            "preflight_loads": 1,
            "warmup_loads": 1 + job["warmups"],
            "warmup_validation_calls": 1 + (job["warmups"] if job["validate"] else 0),
            "timed_loads": 0,
            "sql_count": 0,
            "snapshots": 0,
        },
        "Unexpected serializer/SQL/inspection work",
    )
    require(
        report["runtime_after"]["census_imported"] is False
        and "operations" not in report
        and "inventory" not in report,
        "Probe mixed with latency or inventory",
    )
    require(set(report["prefix_gc"]) == {"preparation", "warmup"}, "Wrong prefix scopes")
    previous_end = 0
    for name in ("preparation", "warmup"):
        scope = report["prefix_gc"][name]
        require(previous_end <= scope["start_ns"], "Prefix scopes overlap")
        previous_end = scope["end_ns"]
        for side in ("before", "after"):
            require(
                scope[side]["enabled"] is True
                and scope[side]["thresholds"] == bundle["gc_policy"]["thresholds"],
                "GC altered before probe",
            )
        for gen in summarize_scope(scope):
            g = gen["generation"]
            require(
                all(
                    scope["after"]["stats"][g][k] - scope["before"]["stats"][g][k] == gen[k]
                    for k in ("collections", "collected", "uncollectable")
                ),
                "Prefix callback mismatch",
            )
    require(previous_end <= report["probe"]["start_ns"], "Warmup overlaps measurement")
    validate_probe(report["probe"], bundle["gc_policy"])
    return report["source_sha256"]


def distribution(values):
    return {
        "n": len(values),
        "mean_ms": statistics.fmean(values) if values else None,
        "median_ms": statistics.median(values) if values else None,
        "min_ms": min(values) if values else None,
        "max_ms": max(values) if values else None,
    }


def analyze(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text())
    require(
        manifest.get("complete") is True
        and manifest.get("experiment") == "full_gc_probe_matrix"
        and manifest.get("schema_version") == 1,
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
        len(plan) == len(runs) == manifest["expected_workers"] == manifest["repeats"] * 4,
        "Incomplete matrix",
    )
    sources, previous_end = None, 0
    paths, pids, pairs = set(), set(), []
    for offset in range(0, len(runs), 2):
        first = plan[offset]
        pair = {
            "pair_id": first["pair_id"],
            "repeat": first["repeat"],
            "validate": first["validate"],
            "pair_order": first["pair_order"],
            "workers": {},
        }
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
            probe = report["probe"]
            pair["workers"][job["profile"]] = {
                "report": entry["report"],
                "elapsed_ms": probe["elapsed_ns"] / 1e6,
                "measured_return": probe["measured_return"],
                "preparation_return": probe["preparation_return"],
                "empty": probe["measured_return"] == 0,
            }
        full, lean = (pair["workers"][p] for p in ("autoload", "django_only"))
        pair["django_only_over_autoload"] = lean["elapsed_ms"] / full["elapsed_ms"]
        pair["empty_pair_ratio"] = (
            pair["django_only_over_autoload"] if full["empty"] and lean["empty"] else None
        )
        pairs.append(pair)
    summaries = []
    for validate in (False, True):
        for profile in ("autoload", "django_only"):
            workers = [p["workers"][profile] for p in pairs if p["validate"] == validate]
            summaries.append(
                {
                    "validate": validate,
                    "profile": profile,
                    "all": distribution([w["elapsed_ms"] for w in workers]),
                    "empty": distribution([w["elapsed_ms"] for w in workers if w["empty"]]),
                    "nonempty": distribution([w["elapsed_ms"] for w in workers if not w["empty"]]),
                }
            )
    return {
        "schema_version": 1,
        "experiment": "full_gc_probe_analysis",
        "complete": True,
        "reference": reference,
        "pairs": pairs,
        "summaries": summaries,
        "interpretation": "One explicit collection per fresh worker after a preparatory full collection. Automatic GC is suspended only for this pair. This is not loads latency or automatic collection frequency. Nonempty results remain visible and are excluded only from the empty bucket. Small-sample summaries are descriptive, not confidence intervals.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    result = analyze(args.directory)
    if args.json:
        with args.json.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
    for pair in result["pairs"]:
        print(f"{pair['pair_id']} validation={pair['validate']} order={pair['pair_order']}")
        for profile in pair["pair_order"]:
            w = pair["workers"][profile]
            print(
                f"  {profile:11} collect2={w['elapsed_ms']:.3f}ms returned={w['measured_return']} preparation_returned={w['preparation_return']}"
            )
        print(
            f"  django_only/autoload={pair['django_only_over_autoload']:.3f} empty_pair_ratio={pair['empty_pair_ratio']}"
        )
    print("One measured collection per worker; nonempty results are retained separately.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        print(f"Full-GC analysis failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error

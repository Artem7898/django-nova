"""Offline type census comparisons, not latency or object-retention proofs."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.summarize_serializer_environment import check_bundle, within  # noqa: E402
from scripts.summarize_serializer_gc import require, summarize_scope  # noqa: E402

TABLES = (
    "types",
    "functions_by_declared_module",
    "classes_by_declared_module",
    "module_objects_by_name",
)


def check_counts(values):
    return len(values) == 3 and all(type(value) is int and value >= 0 for value in values)


def validate_inventory(inventory, policy):
    counts = inventory["generation_counts"]
    require(check_counts(counts) and sum(counts) == inventory["total"], "Invalid inventory totals")
    require(check_counts(inventory["excluded_observer_counts"]), "Invalid observer exclusions")
    for table in TABLES:
        entries = inventory[table]
        labels = [entry["label"] for entry in entries]
        require(
            all(type(label) is str for label in labels) and labels == sorted(set(labels)),
            "Duplicate/unsorted inventory labels",
        )
        totals = [0, 0, 0]
        for entry in entries:
            require(
                check_counts(entry["generations"]) and sum(entry["generations"]) == entry["total"],
                "Invalid type/module counts",
            )
            totals = [a + b for a, b in zip(totals, entry["generations"], strict=True)]
        require(
            totals == counts
            if table == "types"
            else all(a <= b for a, b in zip(totals, counts, strict=True)),
            "Inventory table does not reconcile",
        )
    probe = inventory["inspection"]
    require(
        probe["automatic_gc_suspended"] is True and probe["forced_collections"] == 0,
        "Wrong inspection policy",
    )
    require(probe["python_threads"] == 1, "Unsupported snapshot environment")
    require(
        type(probe["frozen_objects_before"]) is int
        and probe["frozen_objects_before"] >= 0
        and probe["frozen_objects_before"] == probe["frozen_objects_after"],
        "Permanent generation changed during inspection",
    )
    require(probe["acquisition_order"] == [2, 1, 0], "Wrong enumeration order")
    require(
        probe["policy_before"] == probe["policy_restored"] == policy, "GC policy was not restored"
    )
    require(
        probe["stats_before"]
        == probe["stats_after_acquisition"]
        == probe["stats_after_aggregation"],
        "A collection occurred during inspection",
    )
    require(
        check_counts(probe["raw_generation_counts"])
        and probe["raw_generation_counts"]
        == [a + b for a, b in zip(counts, inventory["excluded_observer_counts"], strict=True)],
        "Observer exclusions do not reconcile",
    )


def validate_report(report, config, reference, bundle, digest, sources=None):
    require(
        report.get("complete") is True
        and report.get("schema_version") == 1
        and report.get("experiment") == "gc_inventory_worker",
        "Incomplete/wrong inventory worker",
    )
    require(report["configuration"] == config, "Wrong checkpoint configuration")
    require(
        report["reference_sha256"] == digest
        and report["payload_bundle_sha256"] == reference["payload_bundle_sha256"],
        "Reference/payload mismatch",
    )
    require(
        report["environment"] == reference["environment"],
        "Environment differs from the previous timing matrix",
    )
    require(
        all(
            report["source_sha256"].get(path) == sha
            for path, sha in reference["source_sha256"].items()
        ),
        "Previous experiment or model source changed",
    )
    require(
        sources is None or report["source_sha256"] == sources,
        "Diagnostic sources changed between workers",
    )
    worker = config["worker"]
    require(worker in ("minimal", "pytest"), "Unknown worker")
    for side in ("runtime_before", "runtime_after"):
        names = report[side]["pytest_modules"]
        require(
            not names if worker == "minimal" else "pytest" in names, "Wrong pytest import isolation"
        )
    require(
        report["plugins"] == ([] if worker == "minimal" else reference["plugins"]),
        "Pytest plugin inventory differs",
    )
    active = config["checkpoint"] == "after_warmup"
    require(config["checkpoint"] in ("startup", "after_warmup"), "Unknown checkpoint")
    require(
        config["rows"] == reference["workload"]["rows"]
        and config["warmups"] == reference["workload"]["warmups"],
        "Workload differs",
    )
    if active:
        require(
            config["hint_variant"] in bundle["payloads"] and type(config["validate"]) is bool,
            "Invalid warmup cell",
        )
        require(
            report["payload_sha256"] == bundle["payloads"][config["hint_variant"]]["sha256"],
            "Restored payload differs",
        )
    else:
        require(
            config["hint_variant"] is None
            and config["validate"] is None
            and "payload_sha256" not in report,
            "Startup worker must not restore a payload",
        )
    work = {
        "preflight_loads": int(active),
        "warmup_loads": 1 + config["warmups"] if active else 0,
        "warmup_validation_calls": 1 + (config["warmups"] if config["validate"] else 0)
        if active
        else 0,
        "timed_operations": 0,
        "sql_count": 0,
        "snapshots": 1,
    }
    require(report["work"] == work, "Inspection executed unexpected work")
    validate_inventory(report["inventory"], reference["environment"]["gc_policy"])
    require(
        set(report["prefix_gc"]) == ({"preparation", "warmup"} if active else set()),
        "Wrong checkpoint scopes",
    )
    previous_end = 0
    for name in ("preparation", "warmup") if active else ():
        scope = report["prefix_gc"][name]
        require(previous_end <= scope["start_ns"], "Prefix scopes overlap")
        previous_end = scope["end_ns"]
        for side in ("before", "after"):
            require(
                scope[side]["enabled"]
                and scope[side]["thresholds"] == bundle["gc_policy"]["thresholds"],
                "GC was changed before the snapshot",
            )
        for gen in summarize_scope(scope):
            before, after = (
                scope["before"]["stats"][gen["generation"]],
                scope["after"]["stats"][gen["generation"]],
            )
            require(
                all(
                    after[k] - before[k] == gen[k]
                    for k in ("collections", "collected", "uncollectable")
                ),
                "Prefix callbacks disagree with GC stats",
            )
    return report["source_sha256"]


def difference(left, right):
    """right - left counts in separate processes; not surviving-object IDs."""
    result = {
        "generation_counts": [
            b - a
            for a, b in zip(left["generation_counts"], right["generation_counts"], strict=True)
        ],
        "total": right["total"] - left["total"],
    }
    for table in TABLES:
        a, b = ({x["label"]: x["generations"] for x in side[table]} for side in (left, right))
        entries = []
        for label in a.keys() | b.keys():
            counts = [
                y - x for x, y in zip(a.get(label, [0, 0, 0]), b.get(label, [0, 0, 0]), strict=True)
            ]
            if any(counts):
                entries.append({"label": label, "generations": counts, "total": sum(counts)})
        result[table] = sorted(
            entries, key=lambda x: (-abs(x["generations"][2]), -abs(x["total"]), x["label"])
        )
    return result


def analyze(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    require(
        manifest.get("complete") is True
        and manifest.get("schema_version") == 1
        and manifest.get("experiment") == "gc_inventory_matrix",
        "Incomplete/wrong matrix",
    )
    raw_reference = (directory / "reference.json").read_bytes()
    digest = hashlib.sha256(raw_reference).hexdigest()
    require(digest == manifest["reference_sha256"], "Reference was modified")
    reference = json.loads(raw_reference)
    raw_bundle = (directory / "payloads/payloads.json").read_bytes()
    require(
        hashlib.sha256(raw_bundle).hexdigest() == reference["payload_bundle_sha256"],
        "Payload bundle changed",
    )
    bundle = json.loads(raw_bundle)
    check_bundle(bundle)
    for item in bundle["payloads"].values():
        data = within(directory / "payloads", item["file"]).read_bytes()
        require(
            len(data) == item["bytes"] and hashlib.sha256(data).hexdigest() == item["sha256"],
            "Payload changed",
        )
    runs, plan = manifest["runs"], manifest["plan"]
    require(
        len(runs) == len(plan) == manifest["expected_workers"] == manifest["repeats"] * 10,
        "Wrong matrix size",
    )
    sources, previous_end = None, 0
    seen, pids, cells, pairs, collected_reports = set(), set(), set(), [], {}
    for offset in range(0, len(runs), 2):
        first, second = plan[offset : offset + 2]
        require(
            {**first, "worker": second["worker"]} == second and first["worker"] != second["worker"],
            "Mismatched pair",
        )
        order = ["minimal", "pytest"] if first["repeat"] % 2 else ["pytest", "minimal"]
        require(
            first["pair_order"] == order == [first["worker"], second["worker"]],
            "Non-alternating pair order",
        )
        cell = (first["checkpoint"], first["hint_variant"], first["validate"], first["repeat"])
        require(cell not in cells, "Duplicate matrix cell")
        cells.add(cell)
        pair = {"configuration": {k: v for k, v in first.items() if k != "worker"}, "workers": {}}
        inventories = {}
        for config, run in zip((first, second), runs[offset : offset + 2], strict=True):
            require(
                run["configuration"] == config and run["returncode"] == 0,
                "Failed/mismatched worker",
            )
            require(
                previous_end <= run["launched_ns"] < run["finished_ns"],
                "Workers did not run sequentially",
            )
            previous_end = run["finished_ns"]
            path = within(directory, run["report"])
            require(path not in seen, "Duplicate report")
            seen.add(path)
            raw = path.read_bytes()
            require(
                hashlib.sha256(raw).hexdigest() == run["report_sha256"], "Worker report changed"
            )
            report = json.loads(raw)
            require(
                report["process_id"] == run["process_id"] and report["process_id"] not in pids,
                "Repeated/wrong process ID",
            )
            pids.add(report["process_id"])
            sources = validate_report(report, config, reference, bundle, digest, sources)
            worker = config["worker"]
            inventories[worker] = report["inventory"]
            collected_reports[(*cell, worker)] = report
            pair["workers"][worker] = {
                "report": run["report"],
                "generation_counts": report["inventory"]["generation_counts"],
                "total": report["inventory"]["total"],
                "frozen_objects_excluded": report["inventory"]["inspection"][
                    "frozen_objects_before"
                ],
                "observer_objects_excluded": report["inventory"]["excluded_observer_counts"],
                "prefix_gc": {
                    name: summarize_scope(scope) for name, scope in report["prefix_gc"].items()
                },
            }
        pair["pytest_minus_minimal"] = difference(inventories["minimal"], inventories["pytest"])
        pairs.append(pair)
    expected = {
        (stage, hint, val, repeat)
        for stage, hint, val in [("startup", None, None)]
        + [("after_warmup", h, v) for h in ("original", "without_instance") for v in (False, True)]
        for repeat in range(1, manifest["repeats"] + 1)
    }
    require(cells == expected, "Missing or unexpected checkpoint cell")
    warmup_deltas = []
    for (stage, hint, val, repeat, worker), report in collected_reports.items():
        if stage == "after_warmup":
            baseline = collected_reports[("startup", None, None, repeat, worker)]
            warmup_deltas.append(
                {
                    "hint_variant": hint,
                    "validate": val,
                    "repeat": repeat,
                    "worker": worker,
                    "startup_process_id": baseline["process_id"],
                    "warmup_process_id": report["process_id"],
                    "after_warmup_minus_startup": difference(
                        baseline["inventory"], report["inventory"]
                    ),
                }
            )
    return {
        "schema_version": 1,
        "experiment": "gc_inventory_analysis",
        "complete": True,
        "pairs": pairs,
        "warmup_deltas": warmup_deltas,
        "reference": reference,
        "interpretation": "Tracked objects can include unreachable cycles pending collection. Type/function/class modules are labels, not allocation ownership. Cross-process count deltas do not prove retention or leaks. Inspection GC suspension occurs after the checkpoint; these reports contain no latency benchmark.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--top", type=int, default=8)
    args = parser.parse_args(argv)
    require(args.top > 0, "top must be positive")
    result = analyze(args.directory)
    if args.json:
        with args.json.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
    for pair in result["pairs"]:
        c = pair["configuration"]
        print(f"{c['pair_id']} {c['checkpoint']} {c['hint_variant']} validation={c['validate']}")
        for worker in c["pair_order"]:
            data = pair["workers"][worker]
            print(
                f"  {worker:7} gen0/1/2={data['generation_counts']} total={data['total']} "
                f"frozen_excluded={data['frozen_objects_excluded']}"
            )
        print("  pytest-minus-minimal; largest absolute gen-2 type deltas:")
        for item in pair["pytest_minus_minimal"]["types"][: args.top]:
            print(f"    {item['label']}: generations={item['generations']} total={item['total']:+}")
    print(
        "Separate-process warmup deltas and module tables are in JSON; these are inventories, not latency or leak measurements."
    )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        print(f"Inventory analysis failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error

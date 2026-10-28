"""Analyze existing serializer-GC JSON; no Django, benchmark reruns or unpickling."""

import argparse
import hashlib
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def span(record):
    start, end = record["start_ns"], record["end_ns"]
    require(type(start) is int and type(end) is int and start <= end, "Invalid interval")
    return start, end


def overlap(window, windows):
    start, end = window
    return sum(max(0, min(end, right) - max(start, left)) for left, right in windows)


def check_windows(windows, bounds):
    previous = bounds[0]
    for start, end in windows:
        require(previous <= start <= end <= bounds[1], "Overlapping or out-of-scope intervals")
        previous = end


def summarize_scope(scope, operations=()):
    require(
        not scope["incomplete_gc_starts"] and not scope["unmatched_gc_stops"],
        "Incomplete GC callbacks",
    )
    bounds = span(scope)
    calls = [span(op) for op in operations]
    validation = []
    all_windows = []
    for op, call in zip(operations, calls, strict=True):
        require(op["elapsed_ns"] == call[1] - call[0], "Elapsed time disagrees with interval")
        all_windows.append(call)
        has_start = "validation_start_ns" in op
        require(has_start == ("validation_end_ns" in op), "Incomplete validation interval")
        if has_start:
            window = span(
                {"start_ns": op["validation_start_ns"], "end_ns": op["validation_end_ns"]}
            )
            validation.append(window)
            all_windows.append(window)
    check_windows(all_windows, bounds)
    events = scope["events"]
    check_windows(sorted(span(event) for event in events), bounds)
    require(all(event["generation"] in (0, 1, 2) for event in events), "Unknown GC generation")
    generations = []
    for generation in range(3):
        selected = [event for event in events if event["generation"] == generation]
        result = {
            "generation": generation,
            "collections": len(selected),
            "collected": 0,
            "uncollectable": 0,
            "duration_ns": 0,
            "call_ns": 0,
            "validation_ns": 0,
            "other_ns": 0,
        }
        for event in selected:
            window = span(event)
            duration = window[1] - window[0]
            call = overlap(window, calls)
            validate = overlap(window, validation)
            require(call + validate <= duration, "GC partition double-counted time")
            require(event["collected"] >= 0 and event["uncollectable"] >= 0, "Negative GC count")
            result["duration_ns"] += duration
            result["call_ns"] += call
            result["validation_ns"] += validate
            result["other_ns"] += duration - call - validate
            result["collected"] += event["collected"]
            result["uncollectable"] += event["uncollectable"]
        # The three time buckets are disjoint; object counts remain process-wide.
        generations.append(result)
    return generations


def analyze_timing(report, slow_ms):
    operations = report["operations"]
    config = report["configuration"]
    require(len(operations) == config["operations"] and operations, "Wrong operation count")
    require(
        [op["index"] for op in operations] == list(range(len(operations))), "Wrong operation order"
    )
    require(
        all(("validation_start_ns" in op) == config["validate"] for op in operations),
        "Validation mode disagrees with samples",
    )
    generations = summarize_scope(report["gc"], operations)
    events = report["gc"]["events"]
    intervals = [span(event) for event in events]
    gen2 = [span(event) for event in events if event["generation"] == 2]
    call_ns = sum(op["elapsed_ns"] for op in operations)
    details = []
    for op in operations:
        window = span(op)
        gc_ns = overlap(window, intervals)
        require(gc_ns == op["gc_overlap_ns"], "Stored GC overlap disagrees with events")
        if op["elapsed_ns"] > slow_ms * 1_000_000:
            details.append(
                {
                    "index": op["index"],
                    "start_ns": op["start_ns"],
                    "end_ns": op["end_ns"],
                    "elapsed_ns": op["elapsed_ns"],
                    "gc_overlap_ns": gc_ns,
                    "gen2_overlap_ns": overlap(window, gen2),
                }
            )
    durations = sorted(op["elapsed_ns"] for op in operations)
    slow_total = sum(op["elapsed_ns"] for op in details)
    gc_total = sum(g["duration_ns"] for g in generations)
    bounds = span(report["gc"])
    lifecycle = None
    if "hint_variant" in config:
        windows = []
        for op in operations:
            end = op["released_ns"]
            require(
                type(end) is int and end >= op.get("validation_end_ns", op["end_ns"]),
                "Invalid release boundary",
            )
            windows.append((op["start_ns"], end))
        check_windows(windows, bounds)
        full = sorted(right - left for left, right in windows)
        slow_windows = [(left, right) for left, right in windows if right - left > slow_ms * 1e6]
        lifecycle = {
            "mean_ms": statistics.fmean(full) / 1e6,
            "p99_ms": full[math.ceil(0.99 * len(full)) - 1] / 1e6,
            "slow_count": len(slow_windows),
            "slow_with_gen2_count": sum(overlap(window, gen2) > 0 for window in slow_windows),
            "meaning": "loads + optional validation + result disposal; excludes sample recording",
        }
    return {
        "lifecycle": lifecycle,
        "scopes": {
            "preparation": summarize_scope(report["preparation_gc"]),
            "warmup": summarize_scope(report["warmup_gc"]),
            "loop": generations,
        },
        "call_mean_ms": statistics.fmean(durations) / 1_000_000,
        "call_p99_ms": durations[math.ceil(0.99 * len(durations)) - 1] / 1_000_000,
        "loop_ms": (bounds[1] - bounds[0]) / 1_000_000,
        "call_gc_time_fraction": sum(g["call_ns"] for g in generations) / call_ns
        if call_ns
        else None,
        "gc_in_loop_time_fraction": gc_total / (bounds[1] - bounds[0])
        if bounds[1] > bounds[0]
        else None,
        "slow": {
            "threshold_ms": slow_ms,
            "count": len(details),
            "with_gen2_count": sum(op["gen2_overlap_ns"] > 0 for op in details),
            "elapsed_ns": slow_total,
            "gen2_overlap_ns": sum(op["gen2_overlap_ns"] for op in details),
            "gen2_time_fraction": sum(op["gen2_overlap_ns"] for op in details) / slow_total
            if slow_total
            else None,
            "operations": details,
        },
    }


def analyze_graph(graph):
    nodes = {node["id"]: node for node in graph["nodes"]}
    require(len(nodes) == len(graph["nodes"]), "Duplicate graph node")
    adjacency = {node: set() for node in nodes}
    reverse = {node: set() for node in nodes}
    edges = graph["edges"]
    for edge in edges:
        source, target = edge["source"], edge["target"]
        require(source in nodes and target in nodes, "Graph edge references an absent node")
        adjacency[source].add(target)
        reverse[target].add(source)

    # Iterative Kosaraju: no recursion limit on large retained object graphs.
    visited, finished = set(), []
    for root in nodes:
        stack = [(root, False)]
        while stack:
            node, expanded = stack.pop()
            if expanded:
                finished.append(node)
            elif node not in visited:
                visited.add(node)
                stack.append((node, True))
                stack.extend((child, False) for child in sorted(adjacency[node], reverse=True))
    visited, cycles = set(), []
    for root in reversed(finished):
        if root in visited:
            continue
        component, stack = set(), [root]
        visited.add(root)
        while stack:
            node = stack.pop()
            component.add(node)
            for child in reverse[node] - visited:
                visited.add(child)
                stack.append(child)
        if len(component) > 1 or root in adjacency[root]:
            cycles.append(component)

    cyclic = set().union(*cycles) if cycles else set()
    reachable, stack = set(cyclic), list(cyclic)
    while stack:
        node = stack.pop()
        for child in adjacency[node] - reachable:
            reachable.add(child)
            stack.append(child)
    patterns = Counter()
    examples = []
    for component in cycles:
        kinds = Counter(nodes[node]["kind"] for node in component)
        internal = [
            edge for edge in edges if edge["source"] in component and edge["target"] in component
        ]
        relations = Counter(edge["relation"] for edge in internal)
        pattern = json.dumps(
            {"kinds": dict(sorted(kinds.items())), "relations": dict(sorted(relations.items()))},
            sort_keys=True,
        )
        patterns[pattern] += 1
        if len(examples) < 3:
            examples.append({"nodes": sorted(component), "edges": internal})
    return {
        "nodes": len(nodes),
        "kinds": dict(sorted(Counter(node["kind"] for node in nodes.values()).items())),
        "cyclic_components": len(cycles),
        "cyclic_nodes": sorted(cyclic),
        "reachable_from_cycles": sorted(reachable),
        "downstream_noncyclic_nodes": sorted(reachable - cyclic),
        "cycle_patterns": [
            {"count": count, **json.loads(pattern)} for pattern, count in sorted(patterns.items())
        ],
        "examples": examples,
    }


def analyze_lifetime(report):
    result = {"preparation_gc": summarize_scope(report["preparation_gc"]), "graphs": {}}
    for name, checkpoint in (("input", "input_dropped"), ("output", "result_dropped")):
        graph = analyze_graph(report[f"{name}_graph"])
        ids = {node["id"] for node in report[f"{name}_graph"]["nodes"]}
        release = report["checkpoints"][checkpoint]
        for side in ("before", "after"):
            snapshot = release[side]
            alive = set(snapshot["alive_nodes"])
            require(len(alive) == snapshot["alive"], "Lifetime alive count mismatch")
            require(
                snapshot["observed"] == graph["nodes"] and alive <= ids,
                "Lifetime snapshot mismatches graph",
            )
        graph["alive_before_collection"] = release["before"]["alive"]
        graph["alive_after_collection"] = release["after"]["alive"]
        graph["alive_after_nodes"] = release["after"]["alive_nodes"]
        graph["global_collected_at_release"] = release["global_collected"]
        result["graphs"][name] = graph
    return result


def analyze(directory, slow_ms=20):
    root = Path(directory).resolve()
    manifest_path = root / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    require(manifest["complete"] is True, "Matrix is incomplete")
    require(
        manifest["experiment"] == "serializer_gc_matrix" and manifest["schema_version"] == 1,
        "Wrong matrix schema",
    )
    require(len(manifest["runs"]) == manifest["expected_workers"] > 0, "Wrong worker count")
    reference = manifest["reference"]
    results, names, variants = [], set(), set()
    for entry in manifest["runs"]:
        path = (root / entry["report"]).resolve()
        require(path.is_relative_to(root), "Worker path escapes report directory")
        require(path.name not in names, "Repeated worker file")
        names.add(path.name)
        require(entry["returncode"] == 0, f"Failed worker: {path.name}")
        raw = path.read_bytes()
        report = json.loads(raw)
        require(
            report["complete"] is True and report["experiment"] == "serializer_gc",
            f"Incomplete/wrong worker: {path.name}",
        )
        require(report["schema_version"] == 1, "Wrong worker schema")
        config = report["configuration"]
        require(config == entry["configuration"], "Worker configuration mismatch")
        require(
            report["environment"] == reference["environment"]
            and report["source_sha256"] == reference["source_sha256"],
            "Worker environment/source mismatch",
        )
        key = json.dumps(config, sort_keys=True)
        require(key not in variants, "Duplicate variant/repeat")
        variants.add(key)
        require(config["mode"] in ("timing", "lifetime"), "Unknown mode")
        analysis = (
            analyze_timing(report, slow_ms)
            if config["mode"] == "timing"
            else analyze_lifetime(report)
        )
        results.append(
            {
                "file": path.name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "configuration": config,
                **({"hint_control": report["hint_control"]} if "hint_variant" in config else {}),
                **analysis,
            }
        )
    hint_pairs = compare_hints(results)
    return {
        "schema_version": 1,
        "experiment": "serializer_gc_analysis",
        "complete": True,
        "input_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "reference": reference,
        "notes": [
            "call/validation/other GC times are disjoint; other includes disposal and observer work.",
            "Object counts are process-wide; overlap is temporal association, not proof of causation.",
            "Graph edges project selected ORM caches; containers and unobserved references are omitted.",
            "Cycle nodes and noncyclic descendants are reported separately.",
        ],
        "runs": results,
        "hint_pairs": hint_pairs,
    }


def compare_hints(runs):
    """Pair identical repeat/configuration cells, never pool validation modes."""
    groups = {}
    for run in runs:
        config = run["configuration"]
        if "hint_variant" not in config:
            continue
        variant = config["hint_variant"]
        require(variant in ("original", "without_instance"), "Unknown hint variant")
        require(
            config["operation"] == "loads" and config["scenario"] == "prefetch_related",
            "Wrong hint experiment",
        )
        key = json.dumps({k: v for k, v in config.items() if k != "hint_variant"}, sort_keys=True)
        group = groups.setdefault(key, {})
        require(variant not in group, "Duplicate hint variant")
        group[variant] = run
    pairs = []
    for key, group in sorted(groups.items()):
        require(set(group) == {"original", "without_instance"}, "Missing hint comparison partner")
        original, control = group["original"], group["without_instance"]
        require(
            original["hint_control"] == control["hint_control"],
            "Hint control data differs between workers",
        )
        proof = original["hint_control"]
        config = json.loads(key)
        require(
            proof["removed_instance_hints"] == proof["articles"] == config["rows"],
            "Wrong removed hint count",
        )
        require(proof["tag_instances"] == config["rows"] * 3, "Wrong loaded tag count")
        require(
            all(
                proof[name] is True
                for name in (
                    "independent_graphs",
                    "original_unchanged",
                    "fields_and_loaded_tags_equal",
                    "roundtrip_equal_without_sql",
                )
            ),
            "Failed semantic control",
        )
        pairs.append(
            {
                "configuration": config,
                "original": original["file"],
                "without_instance": control["file"],
                "semantic_sha256": proof["semantic_sha256"],
            }
        )
    return pairs


def percent(value):
    return "n/a" if value is None else f"{value:.1%}"


def print_analysis(result):
    print(
        f"Validated workers: {len(result['runs'])}; database={result['reference']['environment'].get('database_vendor')}"
    )
    for run in sorted(
        result["runs"],
        key=lambda run: (
            run["configuration"]["mode"],
            run["configuration"]["operation"],
            run["configuration"]["scenario"],
            run["configuration"]["validate"],
            run["configuration"]["repeat"],
            run["configuration"].get("hint_variant", ""),
        ),
    ):
        config = run["configuration"]
        variant = f" hint={config['hint_variant']}" if "hint_variant" in config else ""
        print(
            f"\n{config['mode']} {config['operation']} {config['scenario']} validation={int(config['validate'])} repeat={config['repeat']}{variant}"
        )
        if "hint_control" in run:
            proof = run["hint_control"]
            print(
                f"  control verified: articles={proof['articles']} tags={proof['tag_instances']} removed_hints={proof['removed_instance_hints']} bytes={proof['payload_bytes'][config['hint_variant']]}"
            )
        if config["mode"] == "timing":
            print(
                f"  mean={run['call_mean_ms']:.3f}ms p99={run['call_p99_ms']:.3f}ms call_GC_share={percent(run['call_gc_time_fraction'])}"
            )
            for gen in run["scopes"]["loop"]:
                print(
                    f"  gen={gen['generation']} collections={gen['collections']} collected={gen['collected']} uncollectable={gen['uncollectable']} GC_ms(call/validation/other)={gen['call_ns'] / 1e6:.3f}/{gen['validation_ns'] / 1e6:.3f}/{gen['other_ns'] / 1e6:.3f}"
                )
            slow = run["slow"]
            print(
                f"  slow>{slow['threshold_ms']:g}ms={slow['count']} with_gen2={slow['with_gen2_count']} gen2_share_of_slow_time={percent(slow['gen2_time_fraction'])}"
            )
            if run.get("lifecycle") is not None:
                life = run["lifecycle"]
                print(
                    f"  loads+validation+disposal mean={life['mean_ms']:.3f}ms p99={life['p99_ms']:.3f}ms slow>{slow['threshold_ms']:g}ms={life['slow_count']} with_gen2={life['slow_with_gen2_count']} loop={run['loop_ms']:.3f}ms"
                )
        else:
            for name, graph in run["graphs"].items():
                print(
                    f"  {name}: nodes={graph['nodes']} cycles={graph['cyclic_components']} cyclic_nodes={len(graph['cyclic_nodes'])} downstream={len(graph['downstream_noncyclic_nodes'])} alive_before/after={graph['alive_before_collection']}/{graph['alive_after_collection']}"
                )
                for pattern in graph["cycle_patterns"]:
                    print(
                        f"    {pattern['count']}x kinds={pattern['kinds']} edges={pattern['relations']}"
                    )
    if result.get("hint_pairs"):
        print(f"\nVerified original/control pairs: {len(result['hint_pairs'])}")
    print("\nGC object counts are process-wide; overlap does not establish causation.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Existing directory containing manifest.json")
    parser.add_argument("--json", type=Path, help="New JSON output file; refuses overwrites")
    parser.add_argument("--slow-ms", type=float, default=20)
    args = parser.parse_args(argv)
    try:
        require(
            math.isfinite(args.slow_ms) and args.slow_ms >= 0,
            "slow-ms must be finite and nonnegative",
        )
        result = analyze(args.directory, args.slow_ms)
        if args.json:
            with args.json.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
        print_analysis(result)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Analysis failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

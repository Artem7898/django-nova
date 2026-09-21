"""Print per-repeat summaries and the slowest reads from a diagnostic report."""

import argparse
import json
from pathlib import Path


def gc_lines(diag):
    summary = diag.get("gc_summary")
    if summary is None:
        yield "    GC-all summary absent (older report); raw gc_intervals remain available"
        return
    for item in summary:
        yield (
            f"    GC-all gen={item['generation']} collections={item['collections']} "
            f"collected={item['collected']} uncollectable={item['uncollectable']} "
            f"duration={item['duration_ns'] / 1_000_000:.3f}ms "
            f"overlap_reads={item['read_overlapping_collections']} "
            f"overlap_slow={item['slow_read_overlapping_collections']} "
            f"outside_ops={item['outside_operations_collections']} "
            f"outside_ops_time={item['outside_operations_ns'] / 1_000_000:.3f}ms"
        )


def report_lines(report, top):
    if report.get("run_mode") not in {"diagnostic", "gc_only"} or report.get("schema_version") != 2:
        raise ValueError("Expected a report produced with NOVA_MIX_DIAGNOSTICS=1 or gc")
    if not report.get("complete"):
        raise ValueError("Report is incomplete; resolve the benchmark failure first")
    yield f"Run mode: {report['run_mode']}"
    if report["run_mode"] == "gc_only":
        yield "GC-only: phase times are not measured. Non-GC wall time is not CPU time."
    else:
        yield "Times are inclusive wall times. GC overlaps phases; do not add the columns."
    yield "GC-all counts whole-scope events; collected counts objects in the entire process."
    if "preparation_diagnostics" in report:
        yield "\nPreparation: seed, templates and expected projections"
        yield from gc_lines(report["preparation_diagnostics"])
    for case in sorted(
        report["cases"],
        key=lambda c: (c["backend"], c["scenario"], c["rows"], c["write_every"], c["hot_fraction"]),
    ):
        yield (
            f"\n{case['backend']} {case['scenario']} rows={case['rows']} "
            f"hot={case['hot_fraction']:.0%} write_every={case['write_every']}"
        )
        for pair in case["pairs"]:
            for mode in pair["order"]:
                diag = pair[mode]["diagnostics"]
                reads = diag["reads"]
                slow_ns = reads["slow_total_ns"]
                share = reads["slow_gc_overlap_ns"] / slow_ns if slow_ns else 0
                yield (
                    f"  repeat={pair['repeat'] + 1} {mode:5} n={reads['count']} "
                    f"slow>{reads['threshold_ms']:g}ms={reads['slow_count']} "
                    f"slow_with_gc={reads['slow_with_gc_count']} "
                    f"gc_share_of_slow_time={share:.1%} "
                    f"mean={reads['mean_ms']:.3f}ms p99={reads['sample_p99_ms']:.3f}ms "
                    f"gc_unmatched={diag['unmatched_gc_stops']}/{diag['incomplete_gc_starts']}"
                )
                yield from gc_lines(diag)
                slowest = sorted(reads["slow_reads"], key=lambda op: op["elapsed_ms"], reverse=True)
                for op in slowest[:top]:
                    if report["run_mode"] == "gc_only":
                        yield (
                            f"    index={op['index']:3} {op['outcome']:4} "
                            f"total={op['elapsed_ms']:.3f}ms gc={op['gc_overlap_ms']:.3f}ms "
                            f"non_gc={op['non_gc_ms']:.3f}ms GC generations={op['gc_generations']}"
                        )
                        continue
                    phase = op["phase_ms"]
                    phase_gc = op["phase_gc_overlap_ms"]
                    yield (
                        f"    index={op['index']:3} {op['outcome']:4} "
                        f"total={op['elapsed_ms']:.3f}ms gc={op['gc_overlap_ms']:.3f}ms "
                        f"sql={phase['sql']:.3f}ms dumps={phase['serialize']:.3f}ms "
                        f"loads={phase['deserialize']:.3f}ms client={phase['backend_io']:.3f}ms "
                        f"backend_api={phase['backend_api']:.3f}ms other={op['other_ms']:.3f}ms"
                    )
                    yield (
                        f"      GC generations={op['gc_generations']} "
                        f"in_sql={phase_gc['sql']:.3f}ms "
                        f"in_dumps={phase_gc['serialize']:.3f}ms "
                        f"in_loads={phase_gc['deserialize']:.3f}ms "
                        f"in_client={phase_gc['backend_io']:.3f}ms"
                    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument(
        "--top", type=int, default=3, help="Slow reads per repeat/arm; 0 for summaries"
    )
    args = parser.parse_args()
    if args.top < 0:
        parser.error("--top must be non-negative")
    try:
        report = json.loads(args.report.read_text())
        for line in report_lines(report, args.top):
            print(line)
    except BrokenPipeError:
        return
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()

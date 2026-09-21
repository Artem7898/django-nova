"""Print weak-reference checkpoints from a completed object-lifetime experiment."""

import argparse
import json
from pathlib import Path


def checkpoint_line(label, checkpoint):
    before, after = checkpoint["before"], checkpoint["after"]
    return (
        f"  {label}: watched={before['observed']} "
        f"alive_before_gc={before['alive']} alive_after_gc={after['alive']} "
        f"global_collected={checkpoint['global_collected']}"
    )


def report_lines(report):
    if report.get("run_mode") != "object_lifetime" or not report.get("complete"):
        raise ValueError("Expected a complete object_lifetime report")
    yield "Lifetime observations, not latency measurements. Global GC counts include forced GC."
    preparation = report["preparation"]
    yield "Preparation (PKs and expected primitive fingerprints remain alive):"
    yield checkpoint_line("seed", preparation["seed"]["checkpoint"])
    yield checkpoint_line("expected", preparation["expected"]["checkpoint"])
    yield checkpoint_line("templates retained", preparation["templates"]["retained"])
    yield checkpoint_line("templates dropped", preparation["templates"]["dropped"])
    for case in report["cases"]:
        yield (
            f"\n{case['backend']} {case['scenario']} {case['mode']} rows={case['rows']} "
            f"validate={case['validate']} repeat={case['repeat'] + 1} sql={case['sql_count']}"
        )
        checkpoints = case["checkpoints"]
        yield f"  after dropping rows, query still alive: {checkpoints['after_drop_rows']['alive']}"
        for name in ("with_cache_alive", "after_projection_drop", "after_resource_cleanup"):
            yield checkpoint_line(name, checkpoints[name])
        retained = checkpoints["with_cache_alive"]
        for kind, counts in retained["before"]["by_kind"].items():
            after = retained["after"]["by_kind"][kind]["alive"]
            yield (
                f"    {kind}: watched={counts['observed']} "
                f"after_drop={counts['alive']} after_gc={after}"
            )
        for gen in case["gc"]["gc_summary"]:
            yield (
                f"    GC gen={gen['generation']} collections={gen['collections']} "
                f"collected={gen['collected']} uncollectable={gen['uncollectable']}"
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text())
        for line in report_lines(report):
            print(line)
    except BrokenPipeError:
        return
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()

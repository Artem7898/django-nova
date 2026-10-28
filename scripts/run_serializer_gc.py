"""Run every serializer variant/repeat in a fresh interpreter, sequentially.

Example: uv run --locked python scripts/run_serializer_gc.py --output-dir /tmp/nova-serial
Use --settings tests.example_project.settings for a local SQLite smoke run.
"""

import argparse
import itertools
import json
import os
import random
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPTIONS = {
    "mode": ("timing", "lifetime"),
    "operation": ("loads", "dumps"),
    "scenario": ("plain", "prefetch_related"),
    "validation": ("off", "on"),
}


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="New directory; never overwritten"
    )
    parser.add_argument("--settings", default="tests.example_project.settings_postgres")
    parser.add_argument("--rows", type=positive, default=100)
    parser.add_argument("--operations", type=positive, default=400)
    parser.add_argument("--repeats", type=positive, default=4)
    parser.add_argument("--warmups", type=nonnegative, default=5)
    parser.add_argument("--seed", type=int, default=20261024)
    parser.add_argument(
        "--hint-comparison",
        action="store_true",
        help="Compare original/without-instance payloads; requires loads and prefetch_related",
    )
    for key, choices in OPTIONS.items():
        parser.add_argument(f"--{key}", choices=(*choices, "all"), default="all")
    args = parser.parse_args(argv)
    if args.hint_comparison and (args.operation != "loads" or args.scenario != "prefetch_related"):
        parser.error("--hint-comparison requires --operation loads --scenario prefetch_related")
    return args


def jobs_for(args):
    dimensions = [OPTIONS[k] if getattr(args, k) == "all" else (getattr(args, k),) for k in OPTIONS]
    jobs = []
    for mode, operation, scenario, validation in itertools.product(*dimensions):
        for repeat in range(1, args.repeats + 1):
            jobs.append(
                {
                    "mode": mode,
                    "operation": operation,
                    "scenario": scenario,
                    "validate": validation == "on",
                    "repeat": repeat,
                    "rows": args.rows,
                    "operations": args.operations,
                    "warmups": args.warmups,
                }
            )
    if args.hint_comparison:
        jobs = [
            {**job, "hint_variant": variant}
            for job in jobs
            for variant in ("original", "without_instance")
        ]
    random.Random(args.seed).shuffle(jobs)
    return jobs


def child_environment(config, output):
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("COV_CORE_", "COVERAGE_", "NOVA_SERIAL_")):
            del env[key]
    env.pop("PYTHONTRACEMALLOC", None)
    env["PYTEST_ADDOPTS"] = ""
    env["PYTHONHASHSEED"] = "0"
    for key, value in config.items():
        env[f"NOVA_SERIAL_{key.upper()}"] = str(int(value) if isinstance(value, bool) else value)
    env["NOVA_SERIAL_OUTPUT"] = str(output)
    return env


def check_report(report, config, reference):
    if not report.get("complete") or report.get("experiment") != "serializer_gc":
        raise ValueError("Child report is incomplete or has the wrong experiment")
    if report["configuration"] != config:
        raise ValueError("Child configuration differs from requested variant")
    if report["process_id"] == os.getpid():
        raise ValueError("Worker did not use a separate process")
    if "hint_variant" in config:
        proof = report["hint_control"]
        if not (
            proof["removed_instance_hints"] == proof["articles"] == config["rows"]
            and proof["tag_instances"] == config["rows"] * 3
            and all(
                proof[key] is True
                for key in (
                    "independent_graphs",
                    "original_unchanged",
                    "fields_and_loaded_tags_equal",
                    "roundtrip_equal_without_sql",
                )
            )
            and report["payload_bytes"] == proof["payload_bytes"][config["hint_variant"]]
        ):
            raise ValueError("Invalid hint control proof")
    current = {"environment": report["environment"], "source_sha256": report["source_sha256"]}
    if reference is not None and current != reference:
        raise ValueError("Environment, GC policy or sources changed between workers")
    for scope in (
        report["preparation_gc"],
        *([report["warmup_gc"], report["gc"]] if config["mode"] == "timing" else []),
    ):
        if scope["incomplete_gc_starts"] or scope["unmatched_gc_stops"]:
            raise ValueError("Unmatched GC callbacks; inspect child report")
        for side in ("before", "after"):
            if (
                not scope[side]["enabled"]
                or scope[side]["thresholds"] != report["environment"]["gc_thresholds"]
            ):
                raise ValueError("GC settings changed inside worker")
    if config["mode"] == "lifetime":
        for name in ("input_held", "result_dropped", "input_dropped"):
            for side in ("gc_before", "gc_after"):
                state = report["checkpoints"][name][side]
                if (
                    not state["enabled"]
                    or state["thresholds"] != current["environment"]["gc_thresholds"]
                ):
                    raise ValueError("GC settings changed inside lifetime worker")
    return current


def save_manifest(output, manifest):
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def print_summary(report):
    summary = report["summary"]
    if report["configuration"]["mode"] == "timing":
        gc_counts = "/".join(str(g["collections"]) for g in summary["gc"])
        validation = summary["validation_latency"]["mean_ms"]
        validation_text = "off" if validation is None else f"{validation:.3f}ms"
        print(
            f"  pid={report['process_id']} call_mean={summary['latency']['mean_ms']:.3f}ms"
            f" call_p95={summary['latency']['sample_p95_ms']:.3f}ms"
            f" validation_mean={validation_text} loop={summary['observed_loop_ms']:.3f}ms"
            f" gc0/1/2={gc_counts} collected={sum(g['collected'] for g in summary['gc'])}",
            flush=True,
        )
    else:
        print(
            f"  pid={report['process_id']} observed_input={summary['input_observed']}"
            f" observed_result={summary['result_observed']}"
            f" result_alive_before_gc={summary['result_alive_before_collection']}"
            f" result_alive_after_gc={summary['result_alive_after_collection']}"
            f" input_alive_after_gc={summary['input_alive_after_collection']}",
            flush=True,
        )


def run(args):
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    jobs = jobs_for(args)
    manifest = {
        "schema_version": 1,
        "experiment": "serializer_gc_matrix",
        "complete": False,
        "started_utc": datetime.now(UTC).isoformat(),
        "seed": args.seed,
        "settings": args.settings,
        "expected_workers": len(jobs),
        "runs": [],
    }
    reference = None
    save_manifest(output, manifest)
    try:
        for index, config in enumerate(jobs, 1):
            name = (
                f"{index:03d}-{config['mode']}-{config['operation']}-{config['scenario']}"
                f"-validation{int(config['validate'])}-r{config['repeat']}"
            )
            if "hint_variant" in config:
                name += f"-{config['hint_variant']}"
            report_path, log_path = output / f"{name}.json", output / f"{name}.log"
            command = [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-s",
                "--no-cov",
                "-o",
                "addopts=",
                f"--ds={args.settings}",
                "-W",
                "error::pytest.PytestWarning",
                "--tb=short",
                "benchmarks/serializer_gc.py",
            ]
            entry = {"configuration": config, "report": report_path.name, "log": log_path.name}
            manifest["runs"].append(entry)
            save_manifest(output, manifest)
            print(f"[{index}/{len(jobs)}] {name}", flush=True)
            with log_path.open("w", encoding="utf-8") as log:
                completed = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=child_environment(config, report_path),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            entry["returncode"] = completed.returncode
            if completed.returncode:
                raise RuntimeError(f"Worker failed; see {log_path}")
            report = json.loads(report_path.read_text())
            reference = check_report(report, config, reference)
            entry.update(
                process_id=report["process_id"],
                summary=report["summary"],
                payload_bytes=report["payload_bytes"],
                payload_sha256=report["payload_sha256"],
            )
            print_summary(report)
            save_manifest(output, manifest)
        manifest["reference"] = reference
        manifest["complete"] = True
    finally:
        manifest["finished_utc"] = datetime.now(UTC).isoformat()
        save_manifest(output, manifest)
        print(f"Matrix report: {output / 'manifest.json'}", flush=True)
    return output


def main(argv=None):
    args = arguments(argv)
    try:
        run(args)
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

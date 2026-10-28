"""Paired fresh processes: common serializer loop under Django and pytest."""

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Must be positive")
    return number


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--settings", default="tests.example_project.settings_postgres")
    parser.add_argument("--reference-manifest", type=Path)
    parser.add_argument("--rows", type=positive, default=100)
    parser.add_argument("--operations", type=positive, default=400)
    parser.add_argument("--warmups", type=positive, default=5)
    parser.add_argument("--repeats", type=positive, default=4)
    parser.add_argument("--seed", type=int, default=20261025)
    return parser.parse_args(argv)


def jobs_for(args):
    cells = [
        (hint, validate) for hint in ("original", "without_instance") for validate in (False, True)
    ]
    random.Random(args.seed).shuffle(cells)
    jobs = []
    for cell, (hint, validate) in enumerate(cells, 1):
        for repeat in range(1, args.repeats + 1):
            order = ["minimal", "pytest"] if repeat % 2 else ["pytest", "minimal"]
            for worker in order:
                jobs.append(
                    {
                        "mode": "timing",
                        "operation": "loads",
                        "scenario": "prefetch_related",
                        "hint_variant": hint,
                        "validate": validate,
                        "repeat": repeat,
                        "rows": args.rows,
                        "operations": args.operations,
                        "warmups": args.warmups,
                        "pair_id": f"c{cell}-r{repeat}",
                        "pair_order": order,
                        "worker": worker,
                    }
                )
    return jobs


def child_environment(settings, payload_dir):
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("COV_CORE_", "COVERAGE_", "NOVA_ENV_", "NOVA_SERIAL_")):
            del env[key]
    for key in (
        "PYTHONTRACEMALLOC",
        "PYTHONOPTIMIZE",
        "PYTEST_PLUGINS",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        "PYTEST_CURRENT_TEST",
    ):
        env.pop(key, None)
    env.update(
        PYTHONHASHSEED="0",
        PYTEST_ADDOPTS="",
        NOVA_ENV_BASE_SETTINGS=settings,
        NOVA_ENV_PAYLOAD_DIR=str(payload_dir),
    )
    return env


def pytest_command(settings, module):
    return [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-s",
        "--no-cov",
        "-o",
        "addopts=",
        f"--ds={settings}",
        "-W",
        "error::pytest.PytestWarning",
        "--tb=short",
        module,
    ]


def worker_command(worker):
    if worker == "minimal":
        return [sys.executable, "-m", "benchmarks.serializer_environment"]
    return pytest_command(
        "benchmarks.serializer_environment_settings", "benchmarks/serializer_environment_pytest.py"
    )


def save(output, manifest):
    temporary = output / "manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output / "manifest.json")


def main(argv=None):
    from scripts.summarize_serializer_environment import (
        check_bundle,
        check_reference,
        validate_report,
    )

    args = arguments(argv)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    jobs = jobs_for(args)
    manifest = {
        "schema_version": 1,
        "experiment": "serializer_environment",
        "complete": False,
        "started_utc": datetime.now(UTC).isoformat(),
        "settings": args.settings,
        "seed": args.seed,
        "repeats": args.repeats,
        "expected_workers": len(jobs),
        "plan": jobs,
        "runs": [],
        "payload_directory": "payloads",
    }
    save(output, manifest)
    try:
        env = child_environment(args.settings, output / "payloads")
        env["NOVA_ENV_ROWS"] = str(args.rows)
        command = pytest_command(args.settings, "benchmarks/serializer_environment_payloads.py")
        manifest["producer"] = {"command": command, "log": "producer.log"}
        save(output, manifest)
        with (output / "producer.log").open("x") as log:
            result = subprocess.run(
                command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=False
            )
        manifest["producer"]["returncode"] = result.returncode
        if result.returncode:
            raise RuntimeError(f"Payload preparation failed; inspect {output / 'producer.log'}")
        raw = (output / "payloads/payloads.json").read_bytes()
        bundle = json.loads(raw)
        check_bundle(bundle)
        manifest["payload_bundle_sha256"] = hashlib.sha256(raw).hexdigest()
        if args.reference_manifest:
            previous = args.reference_manifest.read_bytes()
            check_reference(json.loads(previous), bundle, jobs)
            manifest["previous_reference"] = {
                "path": str(args.reference_manifest.resolve()),
                "sha256": hashlib.sha256(previous).hexdigest(),
            }
        save(output, manifest)
        reference = None
        plugin_reference = None
        for index, job in enumerate(jobs, 1):
            stem = f"{index:03}-{job['pair_id']}-{job['worker']}"
            job_path, report_path = output / f"{stem}.job.json", output / f"{stem}.json"
            job_path.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
            child_env = child_environment(args.settings, output / "payloads")
            child_env.update(NOVA_ENV_JOB=str(job_path), NOVA_ENV_OUTPUT=str(report_path))
            command = worker_command(job["worker"])
            entry = {
                "configuration": job,
                "command": command,
                "report": report_path.name,
                "log": f"{stem}.log",
                "launched_ns": time.perf_counter_ns(),
            }
            manifest["runs"].append(entry)
            save(output, manifest)
            print(
                f"[{index}/{len(jobs)}] {job['pair_id']} {job['worker']} {job['hint_variant']} validation={job['validate']}",
                flush=True,
            )
            with (output / entry["log"]).open("x") as log:
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=child_env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            entry.update(returncode=result.returncode, finished_ns=time.perf_counter_ns())
            if result.returncode:
                raise RuntimeError(f"Worker failed; inspect {output / entry['log']}")
            raw_report = report_path.read_bytes()
            report = json.loads(raw_report)
            reference = validate_report(
                report, job, bundle, manifest["payload_bundle_sha256"], reference
            )
            if report["process_id"] == os.getpid():
                raise ValueError("Worker was not a fresh process")
            if job["worker"] == "pytest":
                if plugin_reference is not None and plugin_reference != report["plugins"]:
                    raise ValueError("Pytest plugin inventory changed")
                plugin_reference = report["plugins"]
            entry.update(
                process_id=report["process_id"],
                report_sha256=hashlib.sha256(raw_report).hexdigest(),
            )
            save(output, manifest)
        manifest["complete"] = True
    finally:
        manifest["finished_utc"] = datetime.now(UTC).isoformat()
        save(output, manifest)
        print(f"Matrix report: {output / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError) as error:
        print(f"Experiment failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error

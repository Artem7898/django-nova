"""Snapshot startup and warmup in separate paired diagnostic processes."""

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_serializer_environment import (  # noqa: E402
    child_environment,
    positive,
    pytest_command,
    save,
)


def jobs_for(reference, repeats, seed):
    cells = [("startup", None, None)] + [
        ("after_warmup", hint, validate)
        for hint in ("original", "without_instance")
        for validate in (False, True)
    ]
    random.Random(seed).shuffle(cells)
    jobs = []
    for cell, (checkpoint, hint, validate) in enumerate(cells, 1):
        for repeat in range(1, repeats + 1):
            order = ["minimal", "pytest"] if repeat % 2 else ["pytest", "minimal"]
            for worker in order:
                jobs.append(
                    {
                        "checkpoint": checkpoint,
                        "hint_variant": hint,
                        "validate": validate,
                        "rows": reference["workload"]["rows"],
                        "warmups": reference["workload"]["warmups"],
                        "repeat": repeat,
                        "pair_id": f"c{cell}-r{repeat}",
                        "pair_order": order,
                        "worker": worker,
                    }
                )
    return jobs


def prepare_reference(directory):
    from scripts.summarize_serializer_environment import analyze, within

    directory = directory.resolve()
    # JSON validation and byte hashing only; the parent never unpickles data.
    verified = analyze(directory)
    raw = (directory / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    config = manifest["plan"][0]
    reference = {
        "manifest_path": str(directory / "manifest.json"),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "environment": verified["environment"]["environment"],
        "source_sha256": verified["environment"]["source_sha256"],
        "plugins": verified["plugins"],
        "payload_bundle_sha256": manifest["payload_bundle_sha256"],
        "workload": {key: config[key] for key in ("rows", "warmups", "operations")},
    }
    return reference, within(directory, manifest["payload_directory"])


def command_for(worker):
    if worker == "minimal":
        return [sys.executable, "-m", "benchmarks.gc_inventory"]
    return pytest_command(
        "benchmarks.serializer_environment_settings", "benchmarks/gc_inventory_pytest.py"
    )


def main(argv=None):
    from scripts.summarize_gc_inventory import validate_report

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeats", type=positive, default=4)
    parser.add_argument("--seed", type=int, default=20261025)
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(output)
    print("Validating the previous environment matrix and fixed payloads...", flush=True)
    reference, payload_dir = prepare_reference(args.reference_dir)
    jobs = jobs_for(reference, args.repeats, args.seed)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1,
        "experiment": "gc_inventory_matrix",
        "complete": False,
        "started_utc": datetime.now(UTC).isoformat(),
        "repeats": args.repeats,
        "seed": args.seed,
        "expected_workers": len(jobs),
        "plan": jobs,
        "runs": [],
    }
    save(output, manifest)
    try:
        reference_raw = (json.dumps(reference, indent=2) + "\n").encode()
        (output / "reference.json").write_bytes(reference_raw)
        manifest["reference_sha256"] = hashlib.sha256(reference_raw).hexdigest()
        bundle = json.loads((payload_dir / "payloads.json").read_text())
        (output / "payloads").mkdir()
        for name in ("payloads.json", *(item["file"] for item in bundle["payloads"].values())):
            if not isinstance(name, str) or Path(name).name != name or name in ("", ".", ".."):
                raise ValueError("Expected a payload basename, not a path")
            target = output / "payloads" / name
            shutil.copyfile(payload_dir / name, target)
        save(output, manifest)
        sources = None
        for index, job in enumerate(jobs, 1):
            stem = f"{index:03}-{job['pair_id']}-{job['worker']}"
            job_path, report_path = output / f"{stem}.job.json", output / f"{stem}.json"
            job_path.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
            env = child_environment(reference["environment"]["base_settings"], output / "payloads")
            for key in list(env):
                if key.startswith("NOVA_GC_"):
                    del env[key]
            env.update(
                NOVA_GC_JOB=str(job_path),
                NOVA_GC_OUTPUT=str(report_path),
                NOVA_GC_REFERENCE_SHA256=manifest["reference_sha256"],
            )
            command = command_for(job["worker"])
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
                f"[{index}/{len(jobs)}] {job['pair_id']} {job['worker']} {job['checkpoint']} {job['hint_variant']} validation={job['validate']}",
                flush=True,
            )
            with (output / entry["log"]).open("x") as log:
                result = subprocess.run(
                    command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=False
                )
            entry.update(returncode=result.returncode, finished_ns=time.perf_counter_ns())
            if result.returncode:
                raise RuntimeError(f"Worker failed; inspect {output / entry['log']}")
            raw_report = report_path.read_bytes()
            report = json.loads(raw_report)
            sources = validate_report(
                report, job, reference, bundle, manifest["reference_sha256"], sources
            )
            if report["process_id"] == os.getpid():
                raise ValueError("Worker did not run in a fresh process")
            entry.update(
                process_id=report["process_id"],
                report_sha256=hashlib.sha256(raw_report).hexdigest(),
            )
            save(output, manifest)
        manifest["complete"] = True
    finally:
        manifest["finished_utc"] = datetime.now(UTC).isoformat()
        save(output, manifest)
        print(f"Inventory manifest: {output / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        print(f"Inventory failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error

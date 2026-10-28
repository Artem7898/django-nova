"""Compare one preparatory full collection against none in fresh paired processes."""

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

from scripts import run_pytest_plugin_profile as profiles  # noqa: E402
from scripts.run_serializer_environment import positive, save  # noqa: E402


def prepare_reference(directory):
    from scripts.summarize_pytest_plugin_profile import analyze

    directory = directory.resolve()
    verified = analyze(directory)
    raw = (directory / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    first = json.loads((directory / manifest["runs"][0]["report"]).read_text())
    reference = {
        "manifest_path": str(directory / "manifest.json"),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "environment": verified["reference"]["environment"],
        "source_sha256": first["source_sha256"],
        "plugins": verified["reference"]["plugins"],
        "payload_bundle_sha256": verified["reference"]["payload_bundle_sha256"],
        "workload": verified["reference"]["workload"],
    }
    return reference, directory / "payloads"


def jobs_for(reference, repeats, seed):
    cells = [False, True]
    random.Random(seed).shuffle(cells)
    jobs = []
    for cell, validate in enumerate(cells, 1):
        for repeat in range(1, repeats + 1):
            # Adjacent no/yes pairs within each profile. Four-repeat blocks
            # balance profile order and preparation order independently.
            profile_order = ["autoload", "django_only"]
            if repeat % 4 in (2, 3):
                profile_order.reverse()
            preparation_order = [False, True] if repeat % 2 else [True, False]
            for profile in profile_order:
                for prepare in preparation_order:
                    jobs.append(
                        {
                            "worker": "pytest",
                            "profile": profile,
                            "expected_plugins": profiles.expected_plugins(reference, profile),
                            "mode": "timing",
                            "checkpoint": None,
                            "operation": "loads",
                            "scenario": "prefetch_related",
                            "hint_variant": "without_instance",
                            "validate": validate,
                            "prepare_gc": prepare,
                            **reference["workload"],
                            "repeat": repeat,
                            "block_id": f"c{cell}-r{repeat}",
                            "pair_id": f"c{cell}-r{repeat}-{profile}",
                            "profile_order": profile_order,
                            "pair_order": preparation_order,
                        }
                    )
    return jobs


def command_for(profile):
    return [*profiles.command_for(profile)[:-1], "benchmarks/gc_preparation_test.py"]


def environment_for(reference, payload_dir, job):
    env = profiles.environment_for(reference, payload_dir, job)
    for key in list(env):
        if key.startswith(("NOVA_GC_PREP_", "NOVA_FULL_GC_")):
            del env[key]
    return env


def main(argv=None):
    from scripts.summarize_gc_preparation import validate_report

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeats", type=positive, default=4)
    parser.add_argument("--seed", type=int, default=20261028)
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(output)
    print("Validating the completed pytest-plugin matrix...", flush=True)
    reference, payload_dir = prepare_reference(args.reference_dir)
    jobs = jobs_for(reference, args.repeats, args.seed)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1,
        "experiment": "gc_preparation_matrix",
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
        raw_reference = (json.dumps(reference, indent=2) + "\n").encode()
        (output / "reference.json").write_bytes(raw_reference)
        manifest["reference_sha256"] = hashlib.sha256(raw_reference).hexdigest()
        bundle = json.loads((payload_dir / "payloads.json").read_text())
        (output / "payloads").mkdir()
        for name in ("payloads.json", *(item["file"] for item in bundle["payloads"].values())):
            if not isinstance(name, str) or Path(name).name != name or name in ("", ".", ".."):
                raise ValueError("Expected a payload basename")
            shutil.copyfile(payload_dir / name, output / "payloads" / name)
        sources = None
        for index, job in enumerate(jobs, 1):
            stem = f"{index:03}-{job['pair_id']}-prep{int(job['prepare_gc'])}"
            job_path, report_path = output / f"{stem}.job.json", output / f"{stem}.json"
            job_path.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
            env = environment_for(reference, output / "payloads", job)
            env.update(
                NOVA_GC_PREP_JOB=str(job_path),
                NOVA_GC_PREP_OUTPUT=str(report_path),
                NOVA_GC_PREP_REFERENCE_SHA256=manifest["reference_sha256"],
            )
            command = command_for(job["profile"])
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
                f"[{index}/{len(jobs)}] {job['pair_id']} {job['profile']} gc_preparation "
                f"prepare={job['prepare_gc']} validation={job['validate']}",
                flush=True,
            )
            with (output / entry["log"]).open("x") as log:
                result = subprocess.run(
                    command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=False
                )
            entry.update(returncode=result.returncode, finished_ns=time.perf_counter_ns())
            if result.returncode:
                raise RuntimeError(f"Worker failed; inspect {output / entry['log']}")
            raw = report_path.read_bytes()
            report = json.loads(raw)
            sources = validate_report(
                report, job, reference, bundle, manifest["reference_sha256"], sources
            )
            if report["process_id"] == os.getpid():
                raise ValueError("Expected a fresh child process")
            entry.update(
                process_id=report["process_id"], report_sha256=hashlib.sha256(raw).hexdigest()
            )
            save(output, manifest)
        manifest["complete"] = True
    finally:
        manifest["finished_utc"] = datetime.now(UTC).isoformat()
        save(output, manifest)
        print(f"GC preparation manifest: {output / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        print(f"GC preparation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error

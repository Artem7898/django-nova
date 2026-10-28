"""One disposable pytest/DB process exports trusted, fixed input bytes."""

import gc
import hashlib
import json
import os
from pathlib import Path

import pytest
from benchmarks.queryset_cache_cost import environment, fingerprint, make_query, seed_rows
from benchmarks.serializer_gc import no_sql
from benchmarks.serializer_hint_graph import paired_payloads
from tests.cache.test_related_invalidation import relations as relations

from nova.cache.backends.serializers import PickleSerializer

pytestmark = pytest.mark.benchmark


def export_payloads(case, destination, rows):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    report = {
        "experiment": "serializer_environment_payloads",
        "schema_version": 1,
        "complete": False,
    }
    try:
        keys = seed_rows(case, rows)
        original = list(make_query(case, keys, "prefetch_related"))
        control = list(make_query(case, keys, "prefetch_related"))
        with no_sql():
            payloads, proof = paired_payloads(original, control, PickleSerializer())
            expected = fingerprint(original, "prefetch_related")
        report.update(
            rows=rows,
            hint_control=proof,
            expected=expected,
            environment=environment(),
            gc_policy={
                "enabled": gc.isenabled(),
                "thresholds": list(gc.get_threshold()),
                "debug": gc.get_debug(),
            },
            payloads={},
        )
        for name, payload in payloads.items():
            filename = f"{name}.pickle"
            (destination / filename).write_bytes(payload)
            report["payloads"][name] = {
                "file": filename,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        report["complete"] = True
    finally:
        (destination / "payloads.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
    return report


def test_export_environment_payloads(relations):
    export_payloads(relations, os.environ["NOVA_ENV_PAYLOAD_DIR"], int(os.environ["NOVA_ENV_ROWS"]))

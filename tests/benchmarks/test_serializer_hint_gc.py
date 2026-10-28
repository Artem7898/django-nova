"""Controlled back-reference removal, SQL-free parity and object ownership."""

import gc
import json
from copy import deepcopy

import pytest
from benchmarks import serializer_gc as experiment
from benchmarks import serializer_hint_graph as control
from benchmarks.queryset_cache_cost import make_query, seed_rows
from benchmarks.workload_diagnostics import ArmDiagnostics
from scripts import run_serializer_gc as runner
from scripts import summarize_serializer_gc as analyzer

from nova.cache.backends.serializers import PickleSerializer
from tests.benchmarks.test_serializer_gc import config, forbidden
from tests.benchmarks.test_serializer_gc_analysis import report as synthetic_report
from tests.cache.test_related_invalidation import relations as relations


def independent_rows(case):
    keys = seed_rows(case, 2)
    return [list(make_query(case, keys, "prefetch_related")) for _ in range(2)]


def test_payload_changes_only_control_hint_preserving_fields_tags_and_other_hints(relations):
    original, candidate = independent_rows(relations)
    other_hint = {"routing": "same-opaque-value"}
    queries = [row._prefetched_objects_cache["tags"] for row in candidate]
    for query in queries:
        query._hints["custom"] = other_hint
    originals = [row._prefetched_objects_cache["tags"] for row in original]
    for query in originals:
        query._hints["custom"] = {"routing": "same-opaque-value"}
    serializer = PickleSerializer()
    with experiment.no_sql():
        expected = control.loaded_values(original)
        payloads, proof = control.paired_payloads(original, candidate, serializer)
        assert control.loaded_values(candidate) == expected
        for row, query in zip(original, originals, strict=True):
            assert query._hints["instance"] is row
        for row, query in zip(candidate, queries, strict=True):
            assert row._prefetched_objects_cache["tags"] is query
            assert query._hints == {"custom": other_hint}
            assert query._hints["custom"] is other_hint
        for variant, payload in payloads.items():
            restored = serializer.loads(payload)
            assert control.loaded_values(restored) == expected
            assert all(len(row.tags.all()) == 3 for row in restored)
            assert all(
                ("instance" in row.tags.all()._hints) == (variant == "original") for row in restored
            )
            assert all(row.tags.all()._hints["custom"] == other_hint for row in restored)
    assert proof["removed_instance_hints"] == proof["articles"] == 2
    assert proof["tag_instances"] == 6
    assert payloads["original"] != payloads["without_instance"]
    json.dumps(proof, allow_nan=False)


@pytest.mark.parametrize(
    "fault", ["same_graph", "field", "tag", "missing_hint", "wrong_hint", "shared_hints"]
)
def test_invalid_control_is_rejected_instead_of_silently_compared(relations, fault):
    original, candidate = independent_rows(relations)
    if fault == "same_graph":
        candidate = original[:]
    elif fault == "field":
        # fingerprint() omits author_id; full preparation parity must catch it.
        candidate[0].author_id += 1
    elif fault == "tag":
        candidate[0].tags.all()[0].name = "different"
    elif fault == "missing_hint":
        del candidate[-1].tags.all()._hints["instance"]
    elif fault == "wrong_hint":
        candidate[-1].tags.all()._hints["instance"] = original[-1]
    else:
        candidate[-1].tags.all()._hints = candidate[0].tags.all()._hints
    with experiment.no_sql(), pytest.raises(AssertionError):
        control.paired_payloads(original, candidate, PickleSerializer())
    # Validation of all edges happens before any removal.
    assert candidate[0].tags.all()._hints["instance"] is candidate[0]


@pytest.mark.parametrize("validate", [False, True])
@pytest.mark.parametrize("variant", ["original", "without_instance"])
def test_lifetime_cycle_and_release_control(relations, validate, variant):
    cfg = {**config(validate=validate, mode="lifetime"), "hint_variant": variant}
    serializer = PickleSerializer()
    prepared = experiment.prepare(relations, cfg, serializer)
    before = (gc.isenabled(), gc.get_threshold(), gc.get_debug(), list(gc.callbacks))
    with experiment.no_sql():
        result = experiment.run_lifetime(serializer, prepared, cfg)
    graph = analyzer.analyze_graph(result["output_graph"])
    assert graph["nodes"] == 10  # two Article, two QuerySet, six Tag instances
    assert graph["cyclic_components"] == (2 if variant == "original" else 0)
    assert len(graph["downstream_noncyclic_nodes"]) == (6 if variant == "original" else 0)
    assert result["summary"]["result_alive_before_collection"] == (
        10 if variant == "original" else 0
    )
    assert result["summary"]["result_alive_after_collection"] == 0
    assert result["summary"]["sql_count"] == 0
    assert "operations" not in result
    assert (gc.isenabled(), gc.get_threshold(), gc.get_debug(), list(gc.callbacks)) == before


@pytest.mark.parametrize("validate", [False, True])
@pytest.mark.parametrize("variant", ["original", "without_instance"])
def test_timing_and_preparation_do_not_control_gc_or_add_phase_observers(
    relations, monkeypatch, validate, variant
):
    cfg = {**config(validate=validate), "hint_variant": variant}
    serializer = PickleSerializer()
    before = (gc.isenabled(), gc.get_threshold(), gc.get_debug(), list(gc.callbacks))
    with monkeypatch.context() as guards:
        for name in ("collect", "set_threshold", "enable", "disable", "freeze", "unfreeze"):
            guards.setattr(gc, name, forbidden)
        guards.setattr(experiment, "WeakGraph", forbidden)
        guards.setattr(experiment, "collect_checkpoint", forbidden)
        for name in ("instrument_cache", "wrap", "call"):
            guards.setattr(ArmDiagnostics, name, forbidden)
        prepared = experiment.prepare(relations, cfg, serializer)
        guards.setattr(serializer, "dumps", forbidden)
        result = experiment.run_timing(serializer, prepared, cfg)
    assert (gc.isenabled(), gc.get_threshold(), gc.get_debug(), list(gc.callbacks)) == before
    assert result["summary"]["sql_count"] == 0
    assert len(result["operations"]) == cfg["operations"]
    end = 0
    for op in result["operations"]:
        assert end <= op["start_ns"] <= op["end_ns"] <= op["released_ns"]
        if validate:
            assert (
                op["end_ns"]
                <= op["validation_start_ns"]
                <= op["validation_end_ns"]
                <= op["released_ns"]
            )
        end = op["released_ns"]


def test_preparation_uses_identical_graphs_for_both_variants(relations, monkeypatch):
    original, _candidate = independent_rows(relations)
    monkeypatch.setattr(experiment, "seed_rows", lambda *_: [row.pk for row in original])
    serializer = PickleSerializer()
    prepared = [
        experiment.prepare(relations, {**config(), "hint_variant": v}, serializer)
        for v in ("original", "without_instance")
    ]
    assert prepared[0]["hint_control"] == prepared[1]["hint_control"]
    assert prepared[0]["expected"] == prepared[1]["expected"]
    assert prepared[0]["value"] != prepared[1]["value"]
    assert set(prepared[0]) == {"value", "reference_payload", "expected", "hint_control"}


def test_matrix_adds_only_matched_loads_prefetch_cells(tmp_path, monkeypatch):
    args = runner.arguments(
        [
            "--output-dir",
            str(tmp_path / "new"),
            "--hint-comparison",
            "--operation",
            "loads",
            "--scenario",
            "prefetch_related",
        ]
    )
    jobs = runner.jobs_for(args)
    assert len(jobs) == 32 and jobs == runner.jobs_for(args)
    for variant in ("original", "without_instance"):
        selected = [job for job in jobs if job["hint_variant"] == variant]
        assert len(selected) == 16
        assert len({(j["mode"], j["validate"], j["repeat"]) for j in selected}) == 16
    monkeypatch.setenv("NOVA_SERIAL_HINT_VARIANT", "stale")
    env = runner.child_environment(jobs[0], tmp_path / "out.json")
    assert env["NOVA_SERIAL_HINT_VARIANT"] == jobs[0]["hint_variant"]
    old = runner.jobs_for(runner.arguments(["--output-dir", str(tmp_path / "old")]))
    assert len(old) == 64 and all("hint_variant" not in job for job in old)
    assert "NOVA_SERIAL_HINT_VARIANT" not in runner.child_environment(old[0], tmp_path / "old.json")
    with pytest.raises(SystemExit):
        runner.arguments(["--output-dir", "unused", "--hint-comparison"])


def test_release_cost_is_included_without_changing_call_gc_partition():
    report = synthetic_report()
    report["configuration"]["hint_variant"] = "without_instance"
    report["operations"][0]["released_ns"] = 40
    report["operations"][1]["released_ns"] = 80
    result = analyzer.analyze_timing(report, slow_ms=0.00002)
    assert result["slow"]["count"] == 0  # loads takes only 10 ns
    assert result["lifecycle"]["mean_ms"] == 0.00003
    assert result["lifecycle"]["slow_count"] == 2
    assert result["lifecycle"]["slow_with_gen2_count"] == 1
    assert result["scopes"]["loop"][2]["call_ns"] == 5
    assert result["scopes"]["loop"][2]["other_ns"] == 5
    for end in (19, 51, 101):
        report["operations"][0]["released_ns"] = end
        with pytest.raises(ValueError):
            analyzer.analyze_timing(report, 20)


def test_offline_analysis_pairs_variants_and_rejects_different_data(relations):
    prepared = experiment.prepare(
        relations, {**config(), "hint_variant": "original"}, PickleSerializer()
    )
    runs = [
        {
            "file": f"{variant}.json",
            "configuration": {**config(), "hint_variant": variant},
            "hint_control": deepcopy(prepared["hint_control"]),
        }
        for variant in ("original", "without_instance")
    ]
    assert len(analyzer.compare_hints(runs)) == 1
    with pytest.raises(ValueError, match="partner"):
        analyzer.compare_hints(runs[:1])
    runs[1]["hint_control"]["semantic_sha256"] = "different"
    with pytest.raises(ValueError, match="data differs"):
        analyzer.compare_hints(runs)
    assert analyzer.compare_hints([{"configuration": config()}]) == []


def test_restored_result_cannot_execute_sql_during_preparation(relations):
    original, candidate = independent_rows(relations)
    serializer = PickleSerializer()
    native = serializer.loads

    def broken_loads(payload):
        result = native(payload)
        del result[0]._prefetched_objects_cache["tags"]
        list(result[0].tags.all())  # must fail instead of hiding lazy SQL
        return result

    serializer.loads = broken_loads
    with experiment.no_sql(), pytest.raises(AssertionError, match="SQL is forbidden"):
        control.paired_payloads(original, candidate, serializer)

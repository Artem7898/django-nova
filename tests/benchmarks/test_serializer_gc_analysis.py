"""Offline analysis contracts: interval partitioning, graph cycles, input integrity."""

import json
from copy import deepcopy

import pytest
from scripts import summarize_serializer_gc as summary


def event(start, end, generation=2, collected=7):
    return {
        "start_ns": start,
        "end_ns": end,
        "generation": generation,
        "collected": collected,
        "uncollectable": 0,
    }


def scope(events=()):
    return {
        "start_ns": 0,
        "end_ns": 100,
        "events": list(events),
        "incomplete_gc_starts": 0,
        "unmatched_gc_stops": 0,
    }


def operation(index, start, end, validation=None, overlap=0):
    result = {
        "index": index,
        "start_ns": start,
        "end_ns": end,
        "elapsed_ns": end - start,
        "gc_overlap_ns": overlap,
    }
    if validation:
        result.update(validation_start_ns=validation[0], validation_end_ns=validation[1])
    return result


def graph():
    return {
        "nodes": [
            {"id": i, "kind": kind}
            for i, kind in enumerate(("Article", "QuerySet", "Tag", "Self", "Plain"))
        ],
        "edges": [
            {"source": 0, "target": 1, "relation": "prefetch:tags"},
            {"source": 1, "target": 0, "relation": "hint:instance"},
            {"source": 1, "target": 2, "relation": "query_results"},
            {"source": 3, "target": 3, "relation": "self"},
        ],
    }


def report():
    return {
        "schema_version": 1,
        "complete": True,
        "experiment": "serializer_gc",
        "configuration": {
            "mode": "timing",
            "operation": "loads",
            "scenario": "plain",
            "validate": False,
            "operations": 2,
            "repeat": 1,
        },
        "environment": {"database_vendor": "sqlite"},
        "source_sha256": {"a.py": "abc"},
        "operations": [operation(0, 10, 20, overlap=5), operation(1, 50, 60)],
        "gc": scope([event(15, 25)]),
        "warmup_gc": scope(),
        "preparation_gc": scope(),
    }


def test_gc_partition_is_disjoint_and_counts_objects_once():
    operations = [operation(0, 10, 20, (20, 30)), operation(1, 50, 60)]
    events = [event(5, 10), event(15, 25), event(30, 50), event(55, 60)]
    gen = summary.summarize_scope(scope(events), operations)[2]
    assert (gen["call_ns"], gen["validation_ns"], gen["other_ns"]) == (10, 5, 25)
    assert gen["duration_ns"] == 40
    assert gen["collections"] == 4 and gen["collected"] == 28
    assert sum(gen[key] for key in ("call_ns", "validation_ns", "other_ns")) == gen["duration_ns"]


@pytest.mark.parametrize(
    "fault", ["callback", "event_overlap", "phase_overlap", "outside", "elapsed"]
)
def test_rejects_ambiguous_or_incomplete_intervals(fault):
    s = scope([event(15, 25)])
    operations = [operation(0, 10, 20, (20, 30))]
    if fault == "callback":
        s["unmatched_gc_stops"] = 1
    elif fault == "event_overlap":
        s["events"].append(event(24, 26))
    elif fault == "phase_overlap":
        operations[0]["validation_start_ns"] = 19
    elif fault == "outside":
        s["events"][0]["end_ns"] = 101
    else:
        operations[0]["elapsed_ns"] = 11
    with pytest.raises(ValueError):
        summary.summarize_scope(s, operations)


def test_slow_call_gc_share_uses_intersection_not_entire_collection():
    result = summary.analyze_timing(report(), slow_ms=0)
    assert result["call_gc_time_fraction"] == 0.25
    assert result["slow"]["count"] == 2
    assert result["slow"]["with_gen2_count"] == 1
    assert result["slow"]["gen2_time_fraction"] == 0.25
    assert result["slow"]["gen2_overlap_ns"] == 5
    assert summary.analyze_timing(report(), slow_ms=1)["slow"]["gen2_time_fraction"] is None


def test_stale_precomputed_overlap_is_rejected():
    r = report()
    r["operations"][0]["gc_overlap_ns"] = 0
    with pytest.raises(ValueError, match="Stored GC overlap"):
        summary.analyze_timing(r, 20)


def test_graph_distinguishes_cycle_members_and_downstream_nodes():
    result = summary.analyze_graph(graph())
    assert result["cyclic_components"] == 2
    assert result["cyclic_nodes"] == [0, 1, 3]
    assert result["reachable_from_cycles"] == [0, 1, 2, 3]
    assert result["downstream_noncyclic_nodes"] == [2]
    pattern = next(p for p in result["cycle_patterns"] if "Article" in p["kinds"])
    assert pattern["relations"] == {"hint:instance": 1, "prefetch:tags": 1}


def test_graph_handles_empty_and_large_nonrecursive_traversal():
    assert summary.analyze_graph({"nodes": [], "edges": []})["cyclic_components"] == 0
    count = 1500
    nodes = [{"id": i, "kind": "Node"} for i in range(count)]
    edges = [{"source": i, "target": i + 1, "relation": "next"} for i in range(count - 1)]
    assert summary.analyze_graph({"nodes": nodes, "edges": edges})["cyclic_components"] == 0
    edges.append({"source": count - 1, "target": 0, "relation": "back"})
    result = summary.analyze_graph({"nodes": nodes, "edges": edges})
    assert result["cyclic_components"] == 1 and len(result["cyclic_nodes"]) == count


@pytest.mark.parametrize("fault", ["duplicate", "missing"])
def test_invalid_graph_rejected(fault):
    g = graph()
    if fault == "duplicate":
        g["nodes"].append(g["nodes"][0])
    else:
        g["edges"][0]["target"] = 999
    with pytest.raises(ValueError):
        summary.analyze_graph(g)


def matrix(tmp_path):
    worker = report()
    entry = {
        "configuration": deepcopy(worker["configuration"]),
        "report": "worker.json",
        "returncode": 0,
    }
    manifest = {
        "schema_version": 1,
        "experiment": "serializer_gc_matrix",
        "complete": True,
        "expected_workers": 1,
        "runs": [entry],
        "reference": {
            "environment": deepcopy(worker["environment"]),
            "source_sha256": deepcopy(worker["source_sha256"]),
        },
    }
    return worker, manifest


@pytest.mark.parametrize("invalid", [False, True])
def test_lifetime_joins_weak_graph_and_release_without_assigning_gc_objects(invalid):
    def release(ids):
        return {
            "before": {"observed": len(ids), "alive": len(ids), "alive_nodes": ids},
            "after": {"observed": len(ids), "alive": 0, "alive_nodes": []},
            "global_collected": 1234,
        }

    report = {
        "preparation_gc": scope(),
        "input_graph": {"nodes": [], "edges": []},
        "output_graph": graph(),
        "checkpoints": {"input_dropped": release([]), "result_dropped": release(list(range(5)))},
    }
    if invalid:
        report["checkpoints"]["result_dropped"]["before"]["alive_nodes"][-1] = 999
        with pytest.raises(ValueError, match="snapshot mismatches graph"):
            summary.analyze_lifetime(report)
    else:
        output = summary.analyze_lifetime(report)["graphs"]["output"]
        assert output["nodes"] == 5 and output["global_collected_at_release"] == 1234
        assert output["alive_before_collection"] == 5 and output["alive_after_collection"] == 0


@pytest.mark.parametrize(
    "fault", [None, "incomplete", "configuration", "source", "duplicate", "failed", "escape"]
)
def test_manifest_integrity_and_cli(tmp_path, capsys, fault):
    worker, manifest = matrix(tmp_path)
    if fault == "incomplete":
        manifest["complete"] = False
    elif fault == "configuration":
        worker["configuration"]["repeat"] = 2
    elif fault == "source":
        worker["source_sha256"]["a.py"] = "changed"
    elif fault == "duplicate":
        manifest["runs"].append(deepcopy(manifest["runs"][0]))
        manifest["expected_workers"] = 2
    elif fault == "failed":
        manifest["runs"][0]["returncode"] = 1
    elif fault == "escape":
        manifest["runs"][0]["report"] = "../outside.json"
    (tmp_path / "worker.json").write_text(json.dumps(worker))
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    output = tmp_path / "analysis.json"
    status = summary.main([str(tmp_path), "--json", str(output)])
    assert status == (0 if fault is None else 1)
    if fault is None:
        data = json.loads(output.read_text())
        assert data["complete"] and len(data["runs"]) == 1
        assert "Validated workers: 1" in capsys.readouterr().out
        saved = output.read_bytes()
        assert summary.main([str(tmp_path), "--json", str(output)]) == 1
        assert output.read_bytes() == saved
    else:
        assert not output.exists()
        assert "Analysis failed:" in capsys.readouterr().err

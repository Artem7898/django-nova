"""Verify the observer, not assumptions about immediate Django graph disposal."""

import gc
import json
import weakref

import pytest
from benchmarks.object_lifetime import (
    WeakGraph,
    collect_checkpoint,
    observe_model_initialization,
    require_automatic_gc,
)
from benchmarks.queryset_cache_cost import make_query, seed_rows
from benchmarks.queryset_object_lifetime import preparation_probe, probe_read
from benchmarks.workload_diagnostics import ArmDiagnostics, collection_summary
from django.db import connection
from django.test.utils import CaptureQueriesContext
from scripts.summarize_cache_diagnostics import gc_lines
from scripts.summarize_object_lifetimes import checkpoint_line, report_lines

from tests.cache.test_related_invalidation import relations as relations


class Node:
    pass


def test_observation_and_json_snapshots_do_not_keep_objects_alive():
    graph = WeakGraph()
    node = Node()
    graph.watch(node, "root")
    snapshot = graph.snapshot("alive")
    description = graph.describe()
    reference = weakref.ref(node)
    del node
    assert reference() is None
    assert graph.snapshot("dropped")["alive"] == 0
    json.dumps({"snapshot": snapshot, "description": description}, allow_nan=False)


def test_unhashable_nodes_and_identity_deduplication():
    class Unhashable:
        __hash__ = None

    graph, node = WeakGraph(), Unhashable()
    assert graph.watch(node, "first") == graph.watch(node, "second")
    assert len(graph.nodes) == 1
    assert graph.describe()["nodes"][0]["roles"] == ["first", "second"]


def test_collection_distinguishes_reachable_objects_and_unreachable_cycles():
    graph = WeakGraph()
    node = Node()
    node.cycle = node
    graph.watch(node, "cycle")
    held = collect_checkpoint(graph, "held")
    assert held["after"]["alive"] == 1
    del node
    freed = collect_checkpoint(graph, "released")
    assert freed["after"]["alive"] == 0
    # A previous automatic collection can already have released the cycle.
    assert freed["before"]["alive"] in (0, 1)


def test_collection_preserves_gc_configuration_and_existing_callbacks():
    enabled, thresholds, debug = gc.isenabled(), gc.get_threshold(), gc.get_debug()
    callbacks = list(gc.callbacks)
    collect_checkpoint(WeakGraph(), "empty")
    assert (gc.isenabled(), gc.get_threshold(), gc.get_debug()) == (enabled, thresholds, debug)
    assert gc.callbacks == callbacks


def test_debug_saveall_is_rejected(monkeypatch):
    monkeypatch.setattr(gc, "get_debug", lambda: gc.DEBUG_SAVEALL)
    with pytest.raises(ValueError, match="DEBUG_SAVEALL"):
        require_automatic_gc()


@pytest.mark.parametrize("enabled,threshold", [(False, 700), (True, 0)])
def test_automatic_gc_required(monkeypatch, enabled, threshold):
    monkeypatch.setattr(gc, "isenabled", lambda: enabled)
    monkeypatch.setattr(gc, "get_threshold", lambda: (threshold, 10, 10))
    with pytest.raises(ValueError, match="automatic GC"):
        require_automatic_gc()


def test_graph_inspection_does_not_evaluate_a_queryset_or_lazy_relations(relations):
    graph = WeakGraph()
    query = make_query(relations, [relations.article.pk], "plain")
    with CaptureQueriesContext(connection) as sql:
        graph.capture([relations.article], query)
    assert not sql
    assert query._result_cache is None
    # The fixture's article has cached author/profile; the observer follows only loaded fields.
    assert any(node["kind"].endswith(".article") for node in graph.describe()["nodes"])


def test_graph_traversal_does_not_leave_a_cycle_retaining_the_observer(relations):
    graph = WeakGraph()
    reference = weakref.ref(graph)
    query = make_query(relations, [relations.article.pk], "plain")
    graph.capture([relations.article], query)
    del graph
    assert reference() is None


def test_prefetch_graph_contains_back_edge_without_triggering_sql(relations):
    query = make_query(relations, [relations.article.pk], "prefetch_related")
    rows = list(query)
    graph = WeakGraph()
    with CaptureQueriesContext(connection) as sql:
        graph.capture(rows, query)
    assert not sql
    edges = graph.describe()["edges"]
    assert any(edge["relation"] == "prefetch:tags" for edge in edges)
    assert any(edge["relation"] == "hint:instance" for edge in edges)
    before = graph.snapshot("before")
    assert before["alive"] == before["observed"]
    del rows, query
    assert collect_checkpoint(graph, "dropped")["after"]["alive"] == 0


def test_graph_capture_handles_cycles_without_recursing_forever(relations):
    row = relations.article
    query = make_query(relations, [row.pk], "plain")
    query._result_cache = [row]
    query._hints = {"instance": row}
    row._prefetched_objects_cache = {"probe": query}
    graph = WeakGraph()
    with CaptureQueriesContext(connection) as sql:
        graph.capture([row, row], query)
    assert not sql
    assert len(graph.nodes) < 10
    del row._prefetched_objects_cache


@pytest.mark.parametrize("fail", [False, True])
def test_seed_receiver_is_removed_even_on_exception(relations, fail):
    graph = WeakGraph()
    try:
        with observe_model_initialization(graph, [relations.Tag], "created"):
            tag = relations.Tag(name="transient")
            if fail:
                raise RuntimeError("controlled")
    except RuntimeError:
        pass
    assert len(graph.nodes) == 1
    del tag
    relations.Tag(name="outside")
    assert len(graph.nodes) == 1
    assert graph.snapshot("dropped")["alive"] == 0


def test_preparation_retains_templates_but_not_result_models_after_collection(relations):
    pks, expected, report = preparation_probe(
        relations, {"rows": 2, "scenarios": ["plain", "prefetch_related"]}
    )
    assert len(pks) == 2 and len(expected["prefetch_related"]) == 2
    assert report["seed"]["checkpoint"]["after"]["alive"] == 0
    assert report["expected"]["checkpoint"]["after"]["alive"] == 0
    assert report["templates"]["retained"]["after"]["alive"] == 2
    assert report["templates"]["dropped"]["after"]["alive"] == 0
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("scenario", ["plain", "select_related", "prefetch_related", "json"])
@pytest.mark.parametrize("validate", [False, True])
def test_orm_and_memory_probe_results_can_be_released(relations, scenario, validate):
    from benchmarks.queryset_cache_cost import fingerprint

    pks = seed_rows(relations, 2)
    expected = fingerprint(list(make_query(relations, pks, scenario)), scenario)
    for mode in ("orm", "cache_fill", "cache_hit"):
        result = probe_read(relations, pks, expected, "memory", mode, scenario, validate, 0)
        assert result["checkpoints"]["after_resource_cleanup"]["after"]["alive"] == 0
        assert not result["validation_graph"]["nodes"]
        assert result["gc"]["incomplete_gc_starts"] == 0
        assert result["gc"]["unmatched_gc_stops"] == 0
        json.dumps(result, allow_nan=False)


def event(start, end, generation=2, collected=10):
    return {
        "start_ns": start,
        "end_ns": end,
        "generation": generation,
        "collected": collected,
        "uncollectable": 0,
    }


def op(start, end, kind="read"):
    return {"start_ns": start, "end_ns": end, "kind": kind, "elapsed_ns": end - start}


def test_full_gc_summary_counts_fast_slow_write_and_gap_events_once():
    events = [event(1, 5), event(15, 45), event(52, 58), event(75, 80)]
    operations = [op(0, 10), op(20, 40), op(50, 60, "write")]
    gen2 = collection_summary(events, operations, 0.000015)[2]
    assert gen2["collections"] == 4 and gen2["collected"] == 40
    assert gen2["read_overlapping_collections"] == 2
    assert gen2["slow_read_overlapping_collections"] == 1
    assert gen2["write_overlapping_collections"] == 1
    assert gen2["outside_operations_collections"] == 1
    assert gen2["outside_operations_ns"] == 15  # Gap 5 plus clipped boundaries 10.
    assert gen2["duration_ns"] == 45


def test_one_collection_overlapping_two_reads_is_counted_once():
    summary = collection_summary([event(5, 35)], [op(0, 10), op(20, 40)], 1)[2]
    assert summary["read_overlapping_collections"] == 1
    assert summary["read_overlap_ns"] == 20
    assert summary["outside_operations_ns"] == 10


def test_preparation_scope_counts_collections_without_read_operations():
    with ArmDiagnostics() as observer:
        gc.collect(2)
    report = observer.annotate([], repeat=-1, mode="preparation", slow_ms=20)
    summary = report["gc_summary"][2]
    assert summary["collections"] >= 1
    assert summary["outside_operations_collections"] == summary["collections"]
    assert summary["read_overlapping_collections"] == 0


def test_gc_summary_output_includes_collected_objects_outside_operations():
    summary = collection_summary([event(0, 10, collected=123)], [], 20)
    output = "\n".join(gc_lines({"gc_summary": summary}))
    assert "gen=2 collections=1 collected=123" in output
    assert "outside_ops=1" in output


@pytest.mark.parametrize(
    "report",
    [{"run_mode": "timing", "complete": True}, {"run_mode": "object_lifetime", "complete": False}],
)
def test_lifetime_summary_rejects_other_modes_and_incomplete_runs(report):
    with pytest.raises(ValueError, match="complete object_lifetime"):
        list(report_lines(report))


def test_checkpoint_output_distinguishes_watched_models_and_global_gc_count():
    line = checkpoint_line(
        "sample",
        {"before": {"observed": 10, "alive": 5}, "after": {"alive": 0}, "global_collected": 999},
    )
    assert "watched=10" in line and "alive_before_gc=5 alive_after_gc=0" in line
    assert "global_collected=999" in line

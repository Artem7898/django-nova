"""Measurement correctness and restoration, without latency thresholds."""

import gc
import json
import weakref
from asyncio import CancelledError
from types import SimpleNamespace

import pytest
from benchmarks import queryset_cache_workload as workload
from benchmarks.workload_diagnostics import (
    ArmDiagnostics,
    intersect_intervals,
    interval_ns,
    latency_summary,
    merge_intervals,
    slow_read_summary,
)
from scripts.summarize_cache_diagnostics import report_lines

from nova.cache.queryset_cache import QuerySetCache
from tests.cache.test_queryset_cache_failure_recovery import QueryStub
from tests.cache.test_queryset_detached_reads import native_backend as native_backend
from tests.cache.test_queryset_detached_reads import snapshot_spy


@pytest.mark.parametrize(
    ("intervals", "expected"),
    [
        ([], []),
        ([(8, 10), (1, 4), (3, 9)], [(1, 10)]),
        ([(1, 9), (2, 3), (3, 8)], [(1, 9)]),
        ([(1, 2), (2, 4), (5, 7), (8, 8)], [(1, 4), (5, 7)]),
    ],
)
def test_interval_union_preserves_gaps_and_does_not_double_count(intervals, expected):
    assert merge_intervals(intervals) == expected
    assert interval_ns(intervals) == sum(end - start for start, end in expected)


def test_intersection_clips_boundary_collections_and_excludes_touching_events():
    assert intersect_intervals([(0, 12), (18, 30), (40, 50)], [(10, 20), (30, 40)]) == [
        (10, 12),
        (18, 20),
    ]


def operation(index=0, start=100, end=200, outcome="miss"):
    return {
        "kind": "read",
        "outcome": outcome,
        "index": index,
        "start_ns": start,
        "end_ns": end,
        "elapsed_ns": end - start,
        "sql_count": 2 if outcome != "hit" else 0,
    }


def test_gc_is_clipped_and_nested_phases_are_not_added_twice():
    with ArmDiagnostics() as probe:
        pass
    probe.collections = [
        (90, 115, 0, 1, 2, 0),
        (135, 145, 2, 1, 100, 0),
        (198, 220, 1, 2, 5, 0),
        (250, 260, 0, 1, 0, 0),
    ]
    probe.spans = [
        (0, "backend_api", "reader.set", 105, 195, 1, False),
        (0, "sql", "execute", 110, 150, 1, False),
        (0, "serialize", "reader.dumps", 140, 170, 1, False),
        (0, "backend_io", "generation.write", 170, 190, 1, False),
        (0, "backend_io", "client.set", 175, 185, 1, False),
    ]
    op = operation()
    report = probe.annotate([op], repeat=3, mode="cache", slow_ms=20)
    diag = op["diagnostics"]
    assert (op["repeat"], op["mode"]) == (3, "cache")
    assert diag["gc_overlap_ns"] == 27
    assert diag["phase_ns"]["backend_api"] == 90
    assert diag["phase_ns"]["backend_io"] == 20
    assert diag["phase_gc_overlap_ns"]["sql"] == 15
    assert diag["phase_gc_overlap_ns"]["serialize"] == 5
    assert diag["leaf_union_ns"] == 80
    assert diag["leaf_or_gc_union_ns"] == 92
    assert diag["other_ns"] == 8
    assert len(diag["gc_overlaps"]) == 3
    # Collections outside reads remain available in the arm timeline.
    assert len(report["gc_intervals"]) == 4
    json.dumps({"report": report, "operation": op})


def test_gc_in_validation_gap_is_not_attributed_to_neighboring_reads():
    with ArmDiagnostics() as probe:
        pass
    probe.collections = [(210, 220, 2, 1, 100, 0)]
    ops = [operation(), operation(index=1, start=300, end=400)]
    report = probe.annotate(ops, repeat=0, mode="orm", slow_ms=20)
    assert len(report["gc_intervals"]) == 1
    assert all(op["diagnostics"]["gc_overlap_ns"] == 0 for op in ops)


def test_actual_gc_callback_records_generation_and_preserves_settings():
    enabled, thresholds = gc.isenabled(), gc.get_threshold()
    callbacks = list(gc.callbacks)
    with ArmDiagnostics() as probe:
        gc.collect(0)  # Only this unit test requests a collection.
    assert gc.callbacks == callbacks
    assert gc.isenabled() == enabled and gc.get_threshold() == thresholds
    assert any(event[2] == 0 and event[1] >= event[0] for event in probe.collections)
    assert not probe.pending_gc and probe.unmatched_gc_stops == 0


def test_gc_callback_preserves_primitive_metadata_and_flags_incomplete_events():
    ticks = iter([10, 25, 30, 40])
    probe = ArmDiagnostics(clock=lambda: next(ticks))
    probe._gc_callback("start", {"generation": 2})
    probe._gc_callback("stop", {"generation": 2, "collected": 17, "uncollectable": 3})
    probe._gc_callback("stop", {"generation": 1, "collected": 0, "uncollectable": 0})
    probe._gc_callback("start", {"generation": 0})
    assert probe.collections[0][:3] == (10, 25, 2)
    assert probe.collections[0][4:] == (17, 3)
    assert probe.unmatched_gc_stops == 1 and len(probe.pending_gc) == 1


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, CancelledError])
def test_exception_restores_methods_and_only_removes_own_callback(error_type):
    error = error_type("original failure")

    class Client:
        def set(self):
            raise error

    client = Client()
    callbacks = list(gc.callbacks)

    def added(phase, info):
        pass

    try:
        with pytest.raises(error_type) as caught, ArmDiagnostics() as probe:
            probe.wrap(client, "set", "backend_io", "set")
            gc.callbacks.append(added)
            probe.measure(0, client.set)
        assert caught.value is error
        assert probe.active_index is None
        assert probe.spans[0][-1] is True
        assert "set" not in vars(client)
        assert gc.callbacks == [*callbacks, added]
    finally:
        gc.callbacks.remove(added)


def test_timed_call_does_not_retain_result_graph():
    class Result:
        pass

    with ArmDiagnostics() as probe:
        result, start, end = probe.measure(0, probe.call, "sql", "execute", Result)
        ref = weakref.ref(result)
        assert end >= start
        del result
        assert ref() is None


def test_outside_operations_calls_are_not_timed():
    probe = ArmDiagnostics(clock=lambda: pytest.fail("Unexpected timing"))
    sentinel = object()
    assert probe.call("serialize", "dumps", lambda: sentinel) is sentinel
    assert not probe.spans


@pytest.mark.parametrize("diagnostics_enabled", [False, True])
def test_sql_counter_forwards_execute_arguments_result_and_errors(diagnostics_enabled):
    probe = ArmDiagnostics() if diagnostics_enabled else None
    counter = workload.SQLCounter(probe)
    expected = ("SELECT %s", [1], False, {"connection": object()})
    result = object()
    calls = []

    def execute(*args):
        calls.append(args)
        return result

    if probe is not None:
        probe.begin(0)
    assert counter(execute, *expected) is result
    error = RuntimeError("SQL failure")

    def fail(*args):
        raise error

    with pytest.raises(RuntimeError) as caught:
        counter(fail, *expected)
    assert caught.value is error
    assert counter.count == 2 and calls == [expected]
    if probe is not None:
        assert len(probe.spans) == 2 and probe.spans[-1][-1] is True


def test_backend_probes_keep_ownership_contracts_and_capture_both_read_paths(
    native_backend, monkeypatch
):
    serializer = native_backend._serializer
    cache = QuerySetCache(backend=native_backend)
    spy = snapshot_spy(monkeypatch)
    query = QueryStub({"items": [1]})
    operations = []
    with ArmDiagnostics() as probe:
        probe.instrument_cache(cache, "reader")
        assert native_backend._serializer is serializer
        assert native_backend.stores_detached_values is True
        assert native_backend.returns_detached_values is True
        for index, outcome in enumerate(("miss", "hit")):
            rows, start, end = probe.measure(index, cache.get_or_set, query)
            assert rows == [{"items": [1]}]
            operations.append(operation(index, start, end, outcome))
            rows[0]["items"].append(2)
    probe.annotate(operations, repeat=0, mode="cache", slow_ms=20)
    miss, hit = [op["diagnostics"] for op in operations]
    assert miss["phase_ns"]["serialize"] > 0
    assert miss["phase_ns"]["backend_io"] > 0
    assert hit["phase_ns"]["serialize"] == 0
    assert hit["phase_ns"]["deserialize"] > 0
    assert hit["phase_ns"]["backend_io"] > 0
    spy.assert_not_called()
    assert "set" not in vars(native_backend) and "dumps" not in vars(serializer)
    assert "_get_generation_writer" not in vars(native_backend)


def test_lazily_created_generation_writer_is_instrumented_and_restored():
    client = SimpleNamespace(set=lambda *args, **kwargs: True)

    class Writer:
        def write(self, *args, **kwargs):
            return client.set(*args, **kwargs)

    class Backend:
        _client = client
        _generation_writer = None

        def _get_generation_writer(self):
            if self._generation_writer is None:
                self._generation_writer = Writer()
            return self._generation_writer

    backend = Backend()
    cache = SimpleNamespace(_state=SimpleNamespace(backend=backend))
    with ArmDiagnostics() as probe:
        probe.instrument_cache(cache, "writer")
        assert backend._generation_writer is None
        writer = backend._get_generation_writer()
        assert backend._get_generation_writer() is writer
        probe.measure(1, writer.write, "key", b"token")
    assert len(probe.spans) == 2  # Transport contains client.set: not double-counted.
    assert "write" not in vars(writer)
    assert "_get_generation_writer" not in vars(backend)


def test_slow_summary_uses_strict_threshold_and_actual_gc_overlap():
    with ArmDiagnostics() as probe:
        pass
    ops = [
        operation(0, 0, 20_000_000),
        operation(1, 30_000_000, 60_000_000, "orm"),
        operation(2, 70_000_000, 120_000_000, "hit"),
    ]
    probe.collections = [(40_000_000, 50_000_000, 2, 1, 5, 0)]
    summary = probe.annotate(ops, repeat=0, mode="cache", slow_ms=20)["reads"]
    assert summary["slow_count"] == 2
    assert summary["slow_with_gc_count"] == 1
    assert summary["slow_time_share"] == 0.8
    assert summary["slow_gc_overlap_ns"] == 10_000_000
    assert summary["by_outcome"]["miss"]["slow_count"] == 0
    assert summary["sample_p99_ms"] == 50
    assert len(summary["slow_reads"]) == 2


def test_empty_latency_and_slow_summaries_are_json_safe():
    summary = slow_read_summary([], 20)
    assert summary["count"] == 0 and summary["slow_fraction"] is None
    assert latency_summary([])["sample_p99_ms"] is None
    json.dumps(summary, allow_nan=False)


@pytest.mark.parametrize("flag", ["0", "1"])
def test_diagnostic_configuration_is_explicit(monkeypatch, flag):
    monkeypatch.setenv("NOVA_MIX_BACKENDS", "memory")
    monkeypatch.setenv("NOVA_MIX_DIAGNOSTICS", flag)
    config = workload.configuration()
    assert config["diagnostics"] is (flag == "1")


@pytest.mark.parametrize("flag,slow", [("yes", "20"), ("1", "nan"), ("1", "0")])
def test_invalid_diagnostic_settings_fail_early(monkeypatch, flag, slow):
    monkeypatch.setenv("NOVA_MIX_BACKENDS", "memory")
    monkeypatch.setenv("NOVA_MIX_DIAGNOSTICS", flag)
    monkeypatch.setenv("NOVA_MIX_SLOW_MS", slow)
    with pytest.raises(AssertionError):
        workload.configuration()


@pytest.mark.parametrize(
    "report",
    [
        {"schema_version": 1, "run_mode": "timing", "complete": True},
        {"schema_version": 2, "run_mode": "diagnostic", "complete": False},
    ],
)
def test_summary_rejects_timing_only_and_incomplete_reports(report):
    with pytest.raises(ValueError):
        list(report_lines(report, 3))

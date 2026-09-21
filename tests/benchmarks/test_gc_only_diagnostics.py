"""GC-only observation must not install phase probes or alter workload semantics."""

import gc
import json
import weakref
from asyncio import CancelledError

import pytest
from benchmarks import queryset_cache_workload as workload
from benchmarks.workload_diagnostics import ArmDiagnostics
from scripts.summarize_cache_diagnostics import report_lines

from nova.cache.queryset_cache import QuerySetCache
from tests.cache.test_queryset_cache_failure_recovery import QueryStub
from tests.cache.test_queryset_detached_reads import native_backend as native_backend
from tests.cache.test_queryset_detached_reads import snapshot_spy
from tests.cache.test_related_invalidation import relations as relations


def forbidden(*args, **kwargs):
    pytest.fail("GC-only mode attempted phase observation or changed GC settings")


@pytest.mark.parametrize(
    "flag,mode,enabled,phases",
    [
        ("0", "timing", False, False),
        ("1", "diagnostic", True, True),
        ("gc", "gc_only", True, False),
    ],
)
def test_mode_selection_preserves_existing_defaults(monkeypatch, flag, mode, enabled, phases):
    monkeypatch.setenv("NOVA_MIX_BACKENDS", "memory")
    monkeypatch.setenv("NOVA_MIX_DIAGNOSTICS", flag)
    config = workload.configuration()
    assert config["run_mode"] == mode
    assert config["diagnostics"] is enabled
    assert config["record_phases"] is phases


def test_disabled_probes_never_inspect_targets_or_time_phase_calls():
    probe = ArmDiagnostics(record_phases=False, clock=forbidden)
    probe.begin(0)
    probe.instrument_cache(object(), "reader")
    probe.wrap(object(), "missing", "backend_api", "get")
    token = object()
    assert probe.call("serialize", "dumps", lambda: token) is token
    assert not probe.spans and not probe._wrapped


@pytest.mark.parametrize("many", [False, True])
def test_sql_is_counted_without_phase_timer_even_inside_read(monkeypatch, many):
    probe = ArmDiagnostics(record_phases=False, clock=forbidden)
    monkeypatch.setattr(probe, "call", forbidden)
    probe.begin(0)
    counter = workload.SQLCounter(probe)
    args = ("SELECT %s", [42], many, {"connection": object()})
    captured = []
    token = object()

    def execute(*values):
        captured.append(values)
        return token

    assert counter(execute, *args) is token
    error = RuntimeError("original database error")

    def failing_execute(*values):
        raise error

    with pytest.raises(RuntimeError) as caught:
        counter(failing_execute, *args)
    assert caught.value is error
    assert captured == [args] and counter.count == 2
    assert not probe.spans


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt, CancelledError])
def test_gc_only_cleanup_preserves_other_callbacks_and_gc_policy(error_type):
    before = (gc.isenabled(), gc.get_threshold())
    callbacks = list(gc.callbacks)

    def other(phase, info):
        pass

    error = error_type("original error")

    def fail():
        raise error

    try:
        with pytest.raises(error_type) as caught, ArmDiagnostics(record_phases=False) as probe:
            gc.callbacks.append(other)
            probe.measure(0, fail)
        assert caught.value is error
        assert gc.callbacks == [*callbacks, other]
        assert probe.active_index is None and not probe.spans
        assert (gc.isenabled(), gc.get_threshold()) == before
    finally:
        gc.callbacks.remove(other)


def test_gc_callback_records_collected_counts_without_retaining_read_result():
    class Result:
        pass

    ticks = iter([10, 20, 25, 35, 40, 50])
    with ArmDiagnostics(record_phases=False, clock=lambda: next(ticks)) as probe:

        def read():
            probe._gc_callback("start", {"generation": 2})
            probe._gc_callback("stop", {"generation": 2, "collected": 17, "uncollectable": 0})
            return Result()

        result, start, end = probe.measure(0, read)
        ref = weakref.ref(result)
        del result
        assert ref() is None
    assert (start, end) == (20, 40)
    assert probe.collections[0][:3] == (25, 35, 2)
    assert probe.collections[0][4:] == (17, 0)
    assert not probe.spans


def test_gc_only_report_includes_gaps_and_all_outcomes_without_fake_phase_zeros():
    with ArmDiagnostics(record_phases=False) as probe:
        pass
    ops = [
        {"index": 0, "kind": "read", "outcome": "miss", "start_ns": 0, "end_ns": 30_000_000},
        {
            "index": 1,
            "kind": "read",
            "outcome": "hit",
            "start_ns": 50_000_000,
            "end_ns": 55_000_000,
        },
        {"index": 2, "kind": "write", "start_ns": 70_000_000, "end_ns": 80_000_000},
    ]
    for op in ops:
        op["elapsed_ns"] = op["end_ns"] - op["start_ns"]
    probe.collections = [
        (20_000_000, 40_000_000, 2, 1, 10, 0),  # slow read plus gap
        (51_000_000, 53_000_000, 0, 1, 3, 0),  # fast read
        (60_000_000, 65_000_000, 2, 1, 20, 0),  # entirely outside operations
        (71_000_000, 73_000_000, 1, 1, 7, 0),  # write
    ]
    diag = probe.annotate(ops, repeat=2, mode="cache", slow_ms=20)
    assert diag["record_phases"] is False
    assert all(
        "spans" not in op["diagnostics"] and "phase_ns" not in op["diagnostics"] for op in ops
    )
    assert [op["diagnostics"]["non_gc_ns"] for op in ops] == [20_000_000, 3_000_000, 8_000_000]
    gen2 = diag["gc_summary"][2]
    assert gen2["collections"] == 2 and gen2["collected"] == 30
    assert gen2["outside_operations_collections"] == 1
    assert gen2["outside_operations_ns"] == 15_000_000
    assert diag["gc_summary"][0]["read_overlapping_collections"] == 1
    assert diag["gc_summary"][1]["write_overlapping_collections"] == 1
    assert diag["reads"]["slow_count"] == 1
    report = {
        "schema_version": 2,
        "run_mode": "gc_only",
        "complete": True,
        "cases": [
            {
                "backend": "memory",
                "scenario": "plain",
                "rows": 1,
                "write_every": 0,
                "hot_fraction": 0.5,
                "pairs": [{"repeat": 2, "order": ["cache"], "cache": {"diagnostics": diag}}],
            }
        ],
    }
    text = "\n".join(report_lines(report, top=3))
    assert "Run mode: gc_only" in text and "non_gc=20.000ms" in text
    assert "sql=" not in text and "dumps=" not in text
    json.dumps({"report": report, "operations": ops}, allow_nan=False)


def test_gc_only_leaves_native_backend_methods_and_ownership_unchanged(native_backend, monkeypatch):
    cache = QuerySetCache(backend=native_backend)
    spy = snapshot_spy(monkeypatch)
    originals = (native_backend.get, native_backend.set, native_backend._serializer.dumps)
    query = QueryStub({"items": [1]})
    with ArmDiagnostics(record_phases=False) as probe:
        probe.instrument_cache(cache, "reader")
        assert originals == (
            native_backend.get,
            native_backend.set,
            native_backend._serializer.dumps,
        )
        first, _, _ = probe.measure(0, cache.get_or_set, query)
        first[0]["items"].append(2)
        second, _, _ = probe.measure(1, cache.get_or_set, query)
        assert second == [{"items": [1]}]
    spy.assert_not_called()
    assert not probe.spans and not probe._wrapped


def test_complete_gc_only_matrix_never_installs_phase_probes(
    relations, caplog, monkeypatch, tmp_path
):
    report_path = tmp_path / "gc-only.json"
    settings = {
        "NOVA_MIX_DIAGNOSTICS": "gc",
        "NOVA_MIX_BACKENDS": "memory",
        "NOVA_MIX_SCENARIOS": "plain,prefetch_related",
        "NOVA_MIX_ROWS": "2",
        "NOVA_MIX_READS": "20",
        "NOVA_MIX_REPEATS": "2",
        "NOVA_MIX_HIT_RATES": "0,0.5,1",
        "NOVA_MIX_WRITE_EVERY": "0,10",
        "NOVA_MIX_OUTPUT": str(report_path),
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    for name in ("instrument_cache", "wrap", "call"):
        monkeypatch.setattr(ArmDiagnostics, name, forbidden)
    for name in ("collect", "set_threshold", "enable", "disable", "freeze", "unfreeze"):
        monkeypatch.setattr(gc, name, forbidden)
    before = (gc.isenabled(), gc.get_threshold(), list(gc.callbacks))
    workload.test_measure_mixed_queryset_workload(relations, caplog)
    assert (gc.isenabled(), gc.get_threshold(), list(gc.callbacks)) == before
    report = json.loads(report_path.read_text())
    assert report["complete"] and not report["diagnostics_pending"]
    assert report["run_mode"] == "gc_only" and len(report["cases"]) == 12
    assert report["preparation_diagnostics"]["record_phases"] is False
    for case in report["cases"]:
        for pair in case["pairs"]:
            for mode in pair["order"]:
                arm = pair[mode]
                if mode == "cache":
                    assert arm["summary"]["hits"]["count"] == pair["expected_hits_without_eviction"]
                diag = arm["diagnostics"]
                assert diag["unmatched_gc_stops"] == diag["incomplete_gc_starts"] == 0
                for op in arm["operations"]:
                    assert op["end_ns"] - op["start_ns"] == op["elapsed_ns"]
                    assert op["repeat"] == pair["repeat"]
                    assert "phase_ns" not in op["diagnostics"]
                    if op["kind"] == "read":
                        sql_count = (
                            0
                            if op["outcome"] == "hit"
                            else 2
                            if case["scenario"] == "prefetch_related"
                            else 1
                        )
                        assert op["sql_count"] == sql_count

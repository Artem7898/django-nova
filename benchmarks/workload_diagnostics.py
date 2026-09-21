"""Opt-in, synchronous benchmark instrumentation; no production cache changes.

All timestamps use perf_counter_ns in the same process. Phase durations are
inclusive wall time, not CPU time. GC overlaps phases and is never added to
them as an independent cost. Instrumentation changes allocation/timing; use
the ordinary benchmark to measure performance, this mode to locate pauses.
"""

import gc
import math
import statistics
import time
from contextlib import ExitStack
from functools import wraps
from threading import get_ident
from unittest.mock import patch

PHASES = ("sql", "serialize", "deserialize", "backend_io", "backend_api")
LEAF_PHASES = PHASES[:-1]


def merge_intervals(intervals):
    """Union half-open intervals without double-counting nested calls."""
    merged = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def interval_ns(intervals):
    return sum(end - start for start, end in merge_intervals(intervals))


def intersect_intervals(left, right):
    left, right = merge_intervals(left), merge_intervals(right)
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        start, end = max(left[i][0], right[j][0]), min(left[i][1], right[j][1])
        if start < end:
            result.append((start, end))
        if left[i][1] < right[j][1]:
            i += 1
        else:
            j += 1
    return result


def latency_summary(operations):
    values = sorted(op["elapsed_ns"] / 1_000_000 for op in operations)
    return {
        "count": len(values),
        "mean_ms": statistics.fmean(values) if values else None,
        "median_ms": statistics.median(values) if values else None,
        "sample_p95_ms": values[math.ceil(0.95 * len(values)) - 1] if values else None,
        "sample_p99_ms": values[math.ceil(0.99 * len(values)) - 1] if values else None,
        "max_ms": values[-1] if values else None,
    }


def gc_state():
    return {
        "enabled": gc.isenabled(),
        "thresholds": list(gc.get_threshold()),
        "counts": list(gc.get_count()),
        "stats": gc.get_stats(),
    }


def collection_summary(collections, operations, slow_ms):
    """All completed callbacks in scope, including fast reads and gaps.

    Object counts belong to entire process-wide collections. They cannot be
    assigned to one read merely because the intervals intersect.
    """
    reads = [(op["start_ns"], op["end_ns"]) for op in operations if op["kind"] == "read"]
    slow = [
        (op["start_ns"], op["end_ns"])
        for op in operations
        if op["kind"] == "read" and op["elapsed_ns"] > slow_ms * 1_000_000
    ]
    writes = [(op["start_ns"], op["end_ns"]) for op in operations if op["kind"] == "write"]
    measured = [(op["start_ns"], op["end_ns"]) for op in operations]
    result = []
    for generation in sorted({0, 1, 2} | {e["generation"] for e in collections}):
        events = [e for e in collections if e["generation"] == generation]
        summary = {
            "generation": generation,
            "collections": len(events),
            "collected": sum(e["collected"] for e in events),
            "uncollectable": sum(e["uncollectable"] for e in events),
            "duration_ns": sum(e["end_ns"] - e["start_ns"] for e in events),
            "outside_operations_collections": 0,
            "outside_operations_ns": 0,
        }
        for name, windows in (("read", reads), ("slow_read", slow), ("write", writes)):
            overlaps = [
                interval_ns(intersect_intervals([(e["start_ns"], e["end_ns"])], windows))
                for e in events
            ]
            summary[f"{name}_overlapping_collections"] = sum(ns > 0 for ns in overlaps)
            summary[f"{name}_overlap_ns"] = sum(overlaps)
        for event in events:
            overlap = interval_ns(
                intersect_intervals([(event["start_ns"], event["end_ns"])], measured)
            )
            summary["outside_operations_collections"] += int(overlap == 0)
            summary["outside_operations_ns"] += event["end_ns"] - event["start_ns"] - overlap
        result.append(summary)
    return result


class ArmDiagnostics:
    """Record primitive events only; never retain ORM rows, SQL or payloads."""

    def __init__(self, *, record_phases=True, clock=time.perf_counter_ns):
        self.record_phases = record_phases
        self.clock = clock
        self.spans = []
        self.collections = []
        self.pending_gc = {}
        self.unmatched_gc_stops = 0
        self.active_index = None
        self._wrapped = set()
        self._stack = ExitStack()
        self.before = None
        self.after = None

    def __enter__(self):
        self.before = gc_state()
        self.thread_id = get_ident()
        self.start_ns = self.clock()
        callback = self._gc_callback
        gc.callbacks.append(callback)
        self._stack.callback(gc.callbacks.remove, callback)
        return self

    def __exit__(self, *exc):
        self.active_index = None
        try:
            return self._stack.__exit__(*exc)
        finally:
            self.end_ns = self.clock()
            self.after = gc_state()

    def _gc_callback(self, phase, info):
        now = self.clock()
        key = (get_ident(), info["generation"])
        if phase == "start":
            self.pending_gc[key] = now
        elif phase == "stop":
            start = self.pending_gc.pop(key, None)
            if start is None:
                self.unmatched_gc_stops += 1
            else:
                self.collections.append(
                    (start, now, key[1], key[0], info["collected"], info["uncollectable"])
                )

    def begin(self, index):
        assert self.active_index is None
        self.active_index = index

    def end(self):
        self.active_index = None

    def measure(self, index, function, *args):
        self.begin(index)
        start = self.clock()
        try:
            result = function(*args)
        finally:
            end = self.clock()
            self.end()
        return result, start, end

    def call(self, phase, label, function, *args, **kwargs):
        index = self.active_index
        if not self.record_phases or index is None:
            return function(*args, **kwargs)
        thread_id = get_ident()
        start = self.clock()
        failed = False
        try:
            return function(*args, **kwargs)
        except BaseException:
            failed = True
            raise
        finally:
            end = self.clock()
            self.spans.append((index, phase, label, start, end, thread_id, failed))

    def wrap(self, target, method, phase, label):
        if not self.record_phases:
            return
        identity = (id(target), method)
        if identity in self._wrapped:
            return
        original = getattr(target, method, None)
        if not callable(original):
            return
        self._wrapped.add(identity)

        @wraps(original)
        def measured(*args, **kwargs):
            return self.call(phase, label, original, *args, **kwargs)

        self._stack.enter_context(patch.object(target, method, measured))

    def instrument_cache(self, cache, role):
        if not self.record_phases:
            return
        # Patch methods on the existing instances. Replacing a backend or
        # serializer with a proxy changes Nova's exact-type ownership guards.
        backend = cache._state.backend
        raw = getattr(backend, "backend", backend)
        for name in ("get", "set", "get_generation", "get_generations", "rotate_generation"):
            self.wrap(raw, name, "backend_api", f"{role}.{name}")
        serializer = getattr(raw, "_serializer", None)
        if serializer is not None:
            self.wrap(serializer, "dumps", "serialize", f"{role}.dumps")
            self.wrap(serializer, "loads", "deserialize", f"{role}.loads")
        client = getattr(raw, "_client", None)
        if client is not None:
            for name in ("get", "mget", "get_many", "set", "add", "delete"):
                self.wrap(client, name, "backend_io", f"{role}.client.{name}")
        accessor = getattr(raw, "_get_generation_writer", None)
        if callable(accessor):
            # Do not create a writer early or replace its client. Redis's
            # single-attempt transport bypasses ordinary client.set().
            @wraps(accessor)
            def generation_writer():
                writer = accessor()
                self.wrap(writer, "write", "backend_io", f"{role}.generation.write")
                return writer

            self._stack.enter_context(
                patch.object(raw, "_get_generation_writer", generation_writer)
            )
        writer = getattr(raw, "_generation_writer", None)
        if writer is not None:
            self.wrap(writer, "write", "backend_io", f"{role}.generation.write")

    def annotate(self, operations, *, repeat, mode, slow_ms):
        """Compute intersections after collection has stopped, outside timings."""
        collections = [
            {
                "id": i,
                "start_ns": start,
                "end_ns": end,
                "elapsed_ns": end - start,
                "generation": generation,
                "thread_id": thread,
                "collected": collected,
                "uncollectable": uncollectable,
            }
            for i, (start, end, generation, thread, collected, uncollectable) in enumerate(
                self.collections
            )
        ]
        by_index = {}
        for index, phase, label, start, end, thread, failed in self.spans:
            by_index.setdefault(index, []).append(
                {
                    "phase": phase,
                    "label": label,
                    "start_ns": start,
                    "end_ns": end,
                    "thread_id": thread,
                    "failed": failed,
                }
            )
        for op in operations:
            op.update(repeat=repeat, mode=mode, thread_id=self.thread_id)
            window = [(op["start_ns"], op["end_ns"])]
            overlaps = []
            for event in collections:
                overlap = intersect_intervals([(event["start_ns"], event["end_ns"])], window)
                if overlap:
                    overlaps.append(
                        {
                            "event_id": event["id"],
                            "generation": event["generation"],
                            "thread_id": event["thread_id"],
                            "overlap_ns": interval_ns(overlap),
                        }
                    )
            gc_intervals = intersect_intervals(
                [(c["start_ns"], c["end_ns"]) for c in collections], window
            )
            gc_ns = interval_ns(gc_intervals)
            op["diagnostics"] = {
                "record_phases": self.record_phases,
                "gc_overlaps": overlaps,
                "gc_overlap_ns": gc_ns,
                "non_gc_ns": op["elapsed_ns"] - gc_ns,
            }
            assert 0 <= gc_ns <= op["elapsed_ns"]
            if not self.record_phases:
                continue
            spans = by_index.get(op["index"], [])
            phases = {
                phase: intersect_intervals(
                    [(s["start_ns"], s["end_ns"]) for s in spans if s["phase"] == phase], window
                )
                for phase in PHASES
            }
            leaf_intervals = [interval for phase in LEAF_PHASES for interval in phases[phase]]
            accounted = interval_ns([*leaf_intervals, *gc_intervals])
            op["diagnostics"].update(
                {
                    "spans": spans,
                    "phase_ns": {
                        name: interval_ns(intervals) for name, intervals in phases.items()
                    },
                    "phase_gc_overlap_ns": {
                        name: interval_ns(intersect_intervals(intervals, gc_intervals))
                        for name, intervals in phases.items()
                    },
                    "leaf_union_ns": interval_ns(leaf_intervals),
                    "leaf_or_gc_union_ns": accounted,
                    "other_ns": op["elapsed_ns"] - accounted,
                }
            )
            assert 0 <= accounted <= op["elapsed_ns"]
        return {
            "record_phases": self.record_phases,
            "repeat": repeat,
            "mode": mode,
            "thread_id": self.thread_id,
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "gc_before": self.before,
            "gc_after": self.after,
            "gc_intervals": collections,
            "gc_summary": collection_summary(collections, operations, slow_ms),
            "incomplete_gc_starts": len(self.pending_gc),
            "unmatched_gc_stops": self.unmatched_gc_stops,
            "reads": slow_read_summary(operations, slow_ms),
        }


def slow_read_summary(operations, slow_ms):
    reads = [op for op in operations if op["kind"] == "read"]
    slow = [op for op in reads if op["elapsed_ns"] > slow_ms * 1_000_000]
    total_ns = sum(op["elapsed_ns"] for op in reads)
    slow_ns = sum(op["elapsed_ns"] for op in slow)
    details = []
    for op in slow:
        diag = op["diagnostics"]
        detail = {
            "index": op["index"],
            "outcome": op["outcome"],
            "start_ns": op["start_ns"],
            "end_ns": op["end_ns"],
            "elapsed_ms": op["elapsed_ns"] / 1_000_000,
            "gc_overlap_ms": diag["gc_overlap_ns"] / 1_000_000,
            "gc_generations": sorted({event["generation"] for event in diag["gc_overlaps"]}),
            "non_gc_ms": (op["elapsed_ns"] - diag["gc_overlap_ns"]) / 1_000_000,
        }
        if "phase_ns" in diag:
            detail.update(
                {
                    "phase_ms": {name: ns / 1_000_000 for name, ns in diag["phase_ns"].items()},
                    "phase_gc_overlap_ms": {
                        name: ns / 1_000_000 for name, ns in diag["phase_gc_overlap_ns"].items()
                    },
                    "other_ms": diag["other_ns"] / 1_000_000,
                }
            )
        details.append(detail)
    return {
        **latency_summary(reads),
        "threshold_ms": slow_ms,
        "slow_count": len(slow),
        "slow_fraction": len(slow) / len(reads) if reads else None,
        "slow_time_share": slow_ns / total_ns if total_ns else None,
        "slow_with_gc_count": sum(op["diagnostics"]["gc_overlap_ns"] > 0 for op in slow),
        "slow_gc_overlap_ns": sum(op["diagnostics"]["gc_overlap_ns"] for op in slow),
        "slow_total_ns": slow_ns,
        "by_outcome": {
            name: {
                **latency_summary([op for op in reads if op["outcome"] == name]),
                "slow_count": sum(op["outcome"] == name for op in slow),
            }
            for name in ("hit", "miss", "orm")
        },
        "slow_reads": details,
    }

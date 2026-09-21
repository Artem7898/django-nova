"""Weak-only graph observations. No result repr, lazy relation access or referrers."""

import gc
import time
import weakref
from collections import Counter
from contextlib import contextmanager

from benchmarks.workload_diagnostics import gc_state
from django.db.models import Model, QuerySet
from django.db.models.signals import post_init


class WeakGraph:
    """Keep weak references and primitive edges, never the inspected objects."""

    def __init__(self):
        self.nodes = []
        self.edges = set()
        self._identities = {}

    def watch(self, obj, role):
        identity = id(obj)
        index = self._identities.get(identity)
        if index is not None and self.nodes[index]["ref"]() is obj:
            self.nodes[index]["roles"].add(role)
            return index
        ref = weakref.ref(obj)  # No callbacks; Django models need not be hashable.
        index = len(self.nodes)
        self._identities[identity] = index
        kind = obj._meta.label_lower if isinstance(obj, Model) else type(obj).__qualname__
        self.nodes.append({"ref": ref, "kind": kind, "roles": {role}})
        return index

    def capture(self, rows, query):
        """Walk only materialized ORM caches; containers have no weakref in CPython."""
        visited = set()
        pending = [(rows, "returned_model", None), (query, "input_query", None)]
        # Iterative traversal avoids a recursive closure that would itself
        # create cyclic garbage in the lifetime experiment.
        while pending:
            obj, role, parent = pending.pop()
            if isinstance(obj, (Model, QuerySet)):
                index = self.watch(obj, role)
                if parent is not None:
                    self.edges.add((parent, role, index))
                if id(obj) in visited:
                    continue
                visited.add(id(obj))
                attributes = vars(obj)
                if isinstance(obj, Model):
                    state = attributes.get("_state")
                    fields = vars(state).get("fields_cache", {}) if state is not None else {}
                    pending.extend(
                        (child, f"field:{name}", index) for name, child in fields.items()
                    )
                    pending.extend(
                        (child, f"prefetch:{name}", index)
                        for name, child in attributes.get("_prefetched_objects_cache", {}).items()
                    )
                else:
                    pending.append((attributes.get("_result_cache"), "query_results", index))
                    pending.extend(
                        (child, f"hint:{name}", index)
                        for name, child in attributes.get("_hints", {}).items()
                    )
                    pending.append(
                        (attributes.get("_known_related_objects", {}), "known_related", index)
                    )
            elif isinstance(obj, (list, tuple, dict)):
                if id(obj) in visited:
                    continue
                visited.add(id(obj))
                children = obj.values() if isinstance(obj, dict) else obj
                pending.extend((child, role, parent) for child in children)

    def snapshot(self, label):
        # Do not return live objects, even for a diagnostic detail view.
        alive = [i for i, node in enumerate(self.nodes) if node["ref"]() is not None]
        totals = Counter(node["kind"] for node in self.nodes)
        live_counts = Counter(self.nodes[i]["kind"] for i in alive)
        return {
            "label": label,
            "observed": len(self.nodes),
            "alive": len(alive),
            "alive_nodes": alive,
            "by_kind": {
                kind: {"observed": count, "alive": live_counts[kind]}
                for kind, count in sorted(totals.items())
            },
        }

    def describe(self):
        return {
            "nodes": [
                {"id": i, "kind": node["kind"], "roles": sorted(node["roles"])}
                for i, node in enumerate(self.nodes)
            ],
            "edges": [
                {"source": source, "relation": role, "target": target}
                for source, role, target in sorted(self.edges)
            ],
        }


@contextmanager
def observe_model_initialization(graph, model_classes, role):
    """Observe seed/fingerprint allocations; signal receiver stores weakrefs only."""
    accepted = frozenset(model_classes)

    def observe(sender, instance, **kwargs):
        if sender in accepted:
            graph.watch(instance, role)

    post_init.connect(observe, weak=False)
    try:
        yield
    finally:
        post_init.disconnect(observe)


def collect_checkpoint(graph, label):
    """A lifetime experiment, NEVER a latency sample. Counts are process-wide."""
    before = graph.snapshot(f"{label}:before")
    before_gc = gc_state()
    start = time.perf_counter_ns()
    collected = gc.collect(2)
    end = time.perf_counter_ns()
    return {
        "label": label,
        "before": before,
        "after": graph.snapshot(f"{label}:after"),
        "gc_before": before_gc,
        "gc_after": gc_state(),
        "forced_window": {"start_ns": start, "end_ns": end},
        "global_collected": collected,
    }


def require_automatic_gc():
    if not gc.isenabled() or gc.get_threshold()[0] <= 0:
        raise ValueError("Run with automatic GC enabled and its normal thresholds")
    if gc.get_debug() & gc.DEBUG_SAVEALL:
        raise ValueError("GC DEBUG_SAVEALL retains collected objects; disable it for this probe")

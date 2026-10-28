# GC object inventories after startup and warmup

This diagnostic counts GC-tracked objects in generations 0, 1 and 2 in the
minimal Django and pytest environments from the preceding
[serializer process experiment](serializer-environment.md). It changes no
production serializer, cache code or previous latency worker.

## Checkpoints and matrix

Each checkpoint is a **separate fresh process with one terminal inventory**:

- `startup`: Django setup, benchmark imports, metadata enumeration and serializer
  construction have finished; no payload has been deserialized. This is the
  harness-ready state, not a bare Python interpreter immediately after launch.
- `after_warmup`: the same initialization, payload preflight, and the previous
  load/validate/delete warmup schedule have finished. The preflight restores one
  graph. Warmup restores one always-validated graph followed by `warmups` graphs,
  validated only when that cell enables validation. Every result is deleted at
  the previous disposal boundary. No timed loop follows.

The four warmup cells are original/without-instance-hint payload × validation
off/on. Startup has one common cell. Five cells × two environments × four
repeats produce **40 sequential fresh processes**. Minimal and pytest workers
are adjacent within each pair; odd repeats use minimal then pytest, even
repeats reverse that order. Cell order is seeded. A startup inventory is never
taken inside a warmup worker, because enumeration would perturb its heap.

## Observation boundary

Automatic GC runs normally through startup, preflight and warmup. There is no
forced collection, threshold change, freeze, unfreeze or weak-reference graph.
Preflight and warmup retain GC callback events for reconciliation with collector
statistics; they do not install serializer/backend wrappers or phase observers.
SQL is rejected during preflight, warmup and inspection.

Only after reaching the terminal checkpoint, the inspector temporarily calls
`gc.disable()`. It then enumerates generation 2, generation 1, and generation 0
with `gc.get_objects(generation)`. This prevents its own temporary lists and
counting dictionaries from triggering automatic collection or promotion while
the inventory is assembled. This is an intentional diagnostic intervention,
not a change to the existing latency experiment. The inspector rejects extra
Python threads and checks that collection statistics stay unchanged throughout
enumeration and aggregation. After a successful inventory the raw lists are
released before GC is restored. Restoration runs in `finally` even on failure;
failed inspections are rejected and their workers exit. No latency is measured
afterward.

Known inspector-created containers captured in generation 0 are explicitly
excluded, with raw and excluded totals recorded for reconciliation. Other
diagnostic scaffolding already present before suspension remains part of the
reported heap. The inventory is not a claim of zero observer overhead.

Objects already in a frozen/permanent generation are outside this enumeration.
The report records `frozen_objects_before/after`, requires them to agree, and
prints `frozen_excluded`; it never changes their state. The main `total` covers
generations 0/1/2 only. Keep frozen counts visible when comparing environments.

## Counts and interpretation

Every worker report contains complete, sorted count tables, not just a top-N:

- `types`: concrete type module and qualified name, split by generation.
- `functions_by_declared_module`: Python functions grouped by their declared
  `__module__`.
- `classes_by_declared_module`: classes grouped by declared `__module__`.
- `module_objects_by_name`: tracked module objects grouped by module name.

Reports retain only primitive strings, integers, lists and dictionaries.
Enumeration does not call sampled instances' `repr`, `__sizeof__` or arbitrary
instance attributes. It does not traverse referrers or serialize sampled objects.

The analyzer verifies counts, restored GC policy, complete pairs, unique worker
processes, sequential launches, checksums, package versions, plugin inventory
and unchanged previous experiment sources. It reports two signed comparisons:

1. `pytest_minus_minimal` within each adjacent checkpoint pair.
2. `after_warmup_minus_startup` for the same environment and repeat, using
   **different processes**. Deltas with zero total but different generations
   remain visible. They cannot establish which individual object moved or lived.

Counts include tracked unreachable cycles waiting for collection. They are not
all Python allocations, RSS, retained bytes or proof of a leak. Type and declared
module names are labels, not allocation ownership. A large `builtins.dict` or
`builtins.function` count does not identify the responsible application or plugin.
Different classes can also share a module/qualified-name label.

The new inspector imports and report scaffolding alter the environment relative
to the latency worker. Matching payloads, versions, GC policy and the semantic
warmup schedule does not make their heaps byte-identical. Use this matrix to find
repeatable differences in composition; it does not attribute a previous gen-2
pause duration to one type or prove that reducing an object count improves it.

## Run on the original measurement host

Install this experiment after the serializer-environment experiment. Use the
same interpreter, locked dependencies and repository sources as the completed
reference run. The launcher copies its already-generated trusted payload bytes;
it does not export a new database fixture or start PostgreSQL, Redis or Memcached.
Do not substitute pickle files from an untrusted source.

First verify the code:

```bash
uv run --locked pytest -q tests/benchmarks \
&& uv run --locked pyright src/nova \
&& uv run --locked ruff check . \
&& uv run --locked ruff format --check \
  benchmarks/gc_inventory.py benchmarks/gc_inventory_pytest.py \
  scripts/run_gc_inventory.py scripts/summarize_gc_inventory.py \
  tests/benchmarks/test_gc_inventory.py \
&& git diff --check
```

Then run from the repository, without coverage or another concurrent benchmark:

```bash
set -o pipefail
reference_dir="$HOME/nova-reports/serializer-environment-20261025-032437"
report_dir="$HOME/nova-reports/gc-inventory-$(date +%Y%m%d-%H%M%S)"

uv run --locked python scripts/run_gc_inventory.py \
  --reference-dir "$reference_dir" --output-dir "$report_dir" --repeats 4 \
&& uv run --locked python scripts/summarize_gc_inventory.py \
  "$report_dir" --json "$report_dir/analysis.json" \
  | tee "$report_dir/analysis.txt"
```

Use the actual path to the **completed environment matrix directory**, not the
older hints manifest or the prior analysis JSON. The launcher first validates
that matrix. It inherits row count, warmup count and settings; the reference's
400-operation loop is not executed by this experiment. Existing output
directories are rejected, and failed/interrupted runs retain `complete=false`.
The analyzer refuses incomplete or modified evidence and does not unpickle
payloads. Re-running analysis requires a new JSON output filename.

Archive the entire result directory, including `manifest.json`, `reference.json`,
payloads, worker reports/logs and `analysis.json`. Compare all repeats before
choosing a follow-up. In particular, inspect gen-2 counts and type/module deltas
at startup before attributing differences after warmup to deserialization.

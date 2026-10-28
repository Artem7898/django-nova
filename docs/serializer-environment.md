# Serializer GC: process environment experiment

This diagnostic compares the same `PickleSerializer.loads()` loop in a minimal
Django worker and a pytest worker with the installed pytest plugins. It does
not change production caching, serialization or Django routing hints.

The question is whether the process environment contributes to generation-2
pauses, particularly collections with `collected=0`. A collection that finds
no unreachable objects can still take time. These reports measure elapsed
pause duration; they do not identify every object scanned or measure CPU time.

## Controlled comparison

1. One disposable pytest/database process exports both trusted input payloads:
   the original prefetched graph and an independent graph with only cached tag
   QuerySets' `_hints['instance']` removed. All concrete model values, tag values
   and loaded memberships are checked, including SQL-free round trips.
2. Both measurement workers read **the same files**, checked by byte count and
   SHA-256. Database materialization is absent from both measured processes.
   They use the same Django settings, app registry, serializer and validation
   function, with SQL rejected during input checks, warmup and the measured loop.
3. The minimal entry point calls `django.setup()` directly. It never imports
   the pytest-only exporter or old benchmark. Before and after the experiment it
   rejects imported pytest/plugin modules, active coverage, tracing, profiling,
   tracemalloc and optimized Python. The common observer imports standard
   library support, including `unittest.mock`; this is not a bare interpreter.
4. Both variants call the same function. Boundaries remain `loads start/end`,
   optional validation start/end, and `released_ns` immediately after
   `del result`. Sample dictionaries are appended after disposal. No phase
   wrappers, explicit collections, weakrefs, GC resets, freezing or disabling
   are used in timing. The full interval includes disposal cost.
5. The default matrix contains 32 fresh timing processes: original/control ×
   validation off/on × four repeats × minimal/pytest. Each pair runs adjacent
   and sequentially. Odd repeats use minimal → pytest; even repeats reverse
   the order. Cell ordering is seeded. Default four repeats balance order for
   every cell. Pairing reduces drift; it cannot eliminate scheduling noise.

Both workers record installed package versions, module counts, pytest plugin
inventory where applicable, relevant source hashes, process IDs and GC state.
GC callbacks cover input preflight, warmup and the complete measurement loop,
including fast calls and intervals between calls. Imports, Django startup and
metadata enumeration precede these scopes; their resulting heap is intentionally
retained as part of the environment. The collector's initial counters are
recorded, not synchronized by a forced collection.

## Verify the implementation

Run from the django-nova repository with the preceding serializer/hints
experiment installed. Keep `uv.lock` and the measurement interpreter unchanged.

```bash
uv run --locked pytest -q tests/benchmarks \
&& uv run --locked pyright src/nova \
&& uv run --locked ruff check . \
&& uv run --locked ruff format --check \
  benchmarks/serializer_environment*.py \
  scripts/run_serializer_environment.py \
  scripts/summarize_serializer_environment.py \
  tests/benchmarks/test_serializer_environment.py \
&& git diff --check
```

The tests launch tiny fresh-process matrices and check import isolation, input
identity, disposal, unchanged GC policy, callback cleanup, SQL rejection,
alternating order and rejection of incomplete or modified evidence. They make
no assertions about faster latency or an expected number of collections.

## Run on the original measurement host

Use the same Python environment and a new output directory. PostgreSQL is only
needed by the input exporter. Redis and Memcached are not involved.

```bash
set -o pipefail
report_dir="$HOME/nova-reports/serializer-environment-$(date +%Y%m%d-%H%M%S)"
reference="$HOME/nova-reports/serializer-hints-20261025-023441/manifest.json"

docker compose -p nova-orm-tests -f compose.test.yaml up -d --wait postgres \
&& uv run --locked python scripts/run_serializer_environment.py \
  --settings tests.example_project.settings_postgres \
  --reference-manifest "$reference" \
  --rows 100 --operations 400 --warmups 5 --repeats 4 \
  --output-dir "$report_dir" \
&& uv run --locked python scripts/summarize_serializer_environment.py \
  "$report_dir" --slow-ms 20 --json "$report_dir/analysis.json" \
  | tee "$report_dir/analysis.txt"
```

Set `reference` to the actual previous hints manifest location. The optional
reference check fails before timing if payload hashes, relevant versions,
serializer source, GC policy or operation settings differ. It uses manifest
metadata and never unpickles prior reports. If the file is unavailable, omit
`--reference-manifest`: the new paired workers still share exact bytes, but
continuity with the previous experiment is then **not verified**.

The launcher fixes `PYTHONHASHSEED=0`, removes inherited coverage/profiling and
pytest plugin overrides, and enables normal installed-plugin autoload for the
pytest condition. It does not install or upgrade dependencies. Pytest settings
and local conftest hooks still apply; unexpected SQL or GC policy changes fail
the run. Do not run another benchmark or the main test suite simultaneously.

Use `--settings tests.example_project.settings` for local SQLite verification.
That proves the harness works, not the result on the PostgreSQL measurement
host. Inputs are generated locally by the exporter; do not replace these pickle
files with untrusted downloads.

## Read the output

The output directory contains a completeness manifest, fixed payloads, producer
log, per-worker job/JSON/log files and the offline analysis. Interrupted or
invalid runs remain `complete=false`; existing directories and analysis files
are not overwritten. The offline analyzer checks adjacency, order, full matrix,
process identity, source/environment equality and report/payload checksums.
Callback totals must equal the corresponding `gc.get_stats()` deltas.

Keep validation modes and hint variants separate. For **each adjacent pair**:

- Compare `gen2.loop.collected_zero`: collection count, total time, mean,
  median, p95 and maximum duration. A missing bucket has `count=0` and null
  latency estimates; it is not evidence for zero GC cost. The pair ratio is
  pytest mean divided by minimal mean, only when both are observed.
- Inspect gen-2 with `collected>0` separately, and all generation 0/1/2 counts,
  collected/uncollectable objects and time, including preparation and warmup.
- Compare `loads` and `loads+validation+disposal` mean/p99, whole-loop duration,
  slow-operation count and overlap with gen-2. Collection object counts are
  process-wide and cannot be assigned to the payload alone.
- Check repeats and run order before generalizing. Four pairs are diagnostic
  evidence, not a statistical guarantee. A reduced empty gen-2 pause in the
  minimal worker would support an environment contribution. It would not
  identify a particular plugin or justify production removal of routing hints.

This experiment also changes preparation relative to the earlier benchmark:
database fixture work is moved out of **both** measured processes. Compare the
new paired workers first; do not attribute their difference from old pytest
results solely to removing pytest. If needed, a later isolated experiment can
compare pytest plugin configurations or retained heap composition.

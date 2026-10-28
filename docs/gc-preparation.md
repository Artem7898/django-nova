# Preparatory collection and automatic GC frequency

This diagnostic asks whether one full collection after warmup changes the
subsequent **automatic** generation-2 schedule. Each profile runs the same
control payload with and without a preparatory `gc.collect(2)`. The explicit
call is outside read timing. Automatic GC stays enabled throughout, with
unchanged thresholds and debug flags. Production cache code is unchanged.

## Inputs and comparison order

The reference is the completed [pytest plugin profile](pytest-plugin-profile.md)
matrix. Its existing `without_instance` payload is copied byte-for-byte after
manifest and report validation. Rows, warmups and operation count are inherited:
for the existing report these are 100 rows, 5 warmups and 400 measured reads.
The original model values, loaded tags and absence of the experimental instance
hints are checked without SQL. No new database export is performed.

There are **32 fresh processes** by default:
two profiles × two preparation variants × validation off/on × four repeats.
Profiles retain the previous autoload/explicit-Django settings and installed
packages. The active registrations, runtime versions, old source hashes, GC
policy and payload hashes must match the reference.

Primary adjacent pairs compare **unprepared/prepared within one profile**.
Every validation/repeat block contains both profiles, in this order:

| Repeat | Profile order | Preparation order inside each profile |
|---|---|---|
| 1 | autoload, django_only | unprepared, prepared |
| 2 | django_only, autoload | prepared, unprepared |
| 3 | django_only, autoload | unprepared, prepared |
| 4 | autoload, django_only | prepared, unprepared |

This balances which profile and which preparation variant runs first. Profile
comparisons are also reported within each four-worker block; the primary
preparation comparisons are the adjacent ones. Processes run sequentially.

## Measurement boundaries

1. Preflight restores and validates the trusted payload, then returns, releasing
   its restored graph. SQL is rejected.
2. Both arms use the previous warmup body: one always-validated restore, followed
   by the inherited warmup count with optional validation. Each result is deleted.
3. Both arms enter an identical GC-only observation scope. The prepared arm
   calls `gc.collect(2)` once; the unprepared arm makes no explicit collection.
   The call interval and return value are saved. Automatic collections around
   this boundary are preserved in the scope rather than silently discarded.
4. Both arms execute the previous read-loop body, including `loads`, optional
   validation, `del result`, `released_ns`, and subsequent sample recording.
   Tests compare the warmup and loop ASTs against the preceding implementation.
5. Reports and summaries are built after the loop. Policy, frozen-set count,
   callbacks, plugins and single-Python-thread execution are checked. Callback
   removal is protected by context managers even when a call fails.

There are no serializer/backend phase wrappers, weakrefs, object inventories,
threshold changes, freeze/unfreeze or GC disable/enable calls in this worker.
The instrumentation remains the GC callback and the previous per-read samples.
The optional collection does have its callback recorded; its call duration is
therefore not the callback-free measurement from [Full-GC probes](full-gc-probe.md).

The preparation observer and call-storage buffer exist in both arms. Recording
an actual collection adds an event to the prepared arm. This and other harness
objects contribute to the live graph. Compare pairs from this new experiment;
do not assume that an old worker's absolute counts must remain identical after
adding a new harness.

## Run

Use the same locked environment and previous sources, with no concurrent
benchmark, coverage, tracing, profiling or tracemalloc. This run reuses trusted
pickle payloads and forbids SQL, so running database/cache containers is not
required. Do not supply pickle files from untrusted sources.

```bash
set -o pipefail
reference_dir="$HOME/nova-reports/pytest-plugins-20261025-050322"
report_dir="$HOME/nova-reports/gc-preparation-$(date +%Y%m%d-%H%M%S)"

uv run --locked python scripts/run_gc_preparation.py \
  --reference-dir "$reference_dir" --output-dir "$report_dir" --repeats 4 \
&& uv run --locked python scripts/summarize_gc_preparation.py \
  "$report_dir" --slow-ms 20 --json "$report_dir/analysis.json" \
  | tee "$report_dir/analysis.txt"
```

Run from the repository root. The reference is the complete `pytest-plugins`
directory, not the latest `full-gc` report or a standalone `analysis.json`.
The latter has no automatic read loop to reproduce. Existing output directories
and JSON analysis files are not overwritten; failures leave an incomplete
manifest with the relevant child log. The analyzer never unpickles data.

## Outputs and interpretation

Each worker retains the full GC scopes for preflight, warmup, conditioning and
the measured loop, including cumulative counters and raw events for every
generation. The explicit conditioning event is checked against the requested
call and its returned count. It cannot overlap the measured read scope.

The primary comparison reports, per repeat and validation mode:

- Automatic gen-2 count, count per 100 reads, total gen-2 time and all-generation
  GC time inside the loop. Empty/nonempty gen-2 event summaries remain available.
- Every gen-2 start position, its time from loop start, duration and collected
  counts. Indices are zero-based. Events between reads retain previous/next
  indices; events before/after all reads remain separately identified.
- `loads` latency and the full `loads + validation + disposal` mean/p99, slow
  counts, and GC overlap. The post-load/disposal phase also includes boundary
  overhead; it is not a separate measurement of destructor execution.
- The explicit preparation return value and duration, separately from reads.
  Its cost is excluded from loop latency, not eliminated from the workload.
- Prepared-minus-unprepared differences, followed by descriptive mean, median,
  min and max of those paired differences. Profile contrasts within each block
  are available separately.

GC time overlaps read latency and must not be added to it. All scope events
are retained, including those outside slow reads. Per-run p99 uses 400 reads;
four process repeats are too few for a precise population-level tail claim.

First inspect the unprepared arm: does the earlier difference between profiles
reappear in this harness? Then inspect whether preparation changes only the
first gen-2 position or also the later intervals and total count. A shift of
the first event alone may change the total by one at a finite-loop boundary.

If preparation changes the schedule, that supports a contribution from state
carried into the loop. It does not isolate a single internal counter: a full
collection also changes generation membership, garbage and some free lists.
If the profile difference persists, the initial state alone is insufficient to
explain it under this workload. If the unprepared arm no longer reproduces the
old difference, do not attribute that change to preparation.

This is an experiment, not a recommendation to call `gc.collect()` before
production cache reads. Production thresholds and serializer behavior remain
unchanged.

## Recorded results: 2026-10-28

The completed `gc-preparation-20261028-041956` run contains 32 workers. Its
manifest SHA-256 is
`13a48f8c4b964f168c3ceaff3a65690802d4370e6e55c2f22bbb8cf3c5d691fe`.
Offline validation of the supplied reports and payload hashes succeeded, and
recomputing the analysis reproduced the supplied `analysis.json`. All 25
reported source hashes matched the experimental sources at analysis time.
Raw reports, process logs and pickle payloads are retained outside the source
tree; this section records the conclusions rather than embedding those local
artifacts in the package.

The workers used CPython 3.12.13, Django 5.2.16, the fixed `without_instance`
control with 100 rows, five warmups and 400 measured reads. Automatic GC stayed
enabled with thresholds `(700, 10, 10)` and debug flags zero. Every count and
event sequence below repeated across all four repeats with validation both
enabled and disabled. Timings remain specific to this host and workload.

### Boundary state

`get_count()` checkpoints are scheduling state, not cumulative collection
totals. The latter are reported separately by `get_stats()`.

| Profile | Preparatory collection | After warmup | After conditioning | Before reads |
|---|---|---|---|---|
| autoload | No | `(1, 11, 10)` | `(9, 11, 10)` | `(16, 11, 10)` |
| autoload | Yes | `(1, 11, 10)` | `(2, 0, 0)` | `(16, 0, 0)` |
| django_only | No | `(1, 11, 9)` | `(9, 11, 9)` | `(16, 11, 9)` |
| django_only | Yes | `(1, 11, 9)` | `(2, 0, 0)` | `(16, 0, 0)` |

After warmup, cumulative gen-0/gen-1/gen-2 collection totals were
`(242, 21, 1)` for autoload and `(231, 20, 1)` for django_only. Their cumulative
collected-object totals were `(1417, 237, 10)` and `(1278, 283, 0)` respectively.
Warmup itself contributed the same 34 gen-0 and three gen-1 events in both
profiles, collecting zero objects. These cumulative differences therefore
preceded warmup.

Conditioning without an explicit collection left cumulative statistics
unchanged. Prepared workers added exactly one gen-2 event and 59 collected
objects. Statistics did not change between the end of conditioning and the
beginning of reads; the first count component changed without another
collection on that transition.

### Automatic events in the read loop

Counts below classify each callback event by its reported generation. The
preparatory collection is excluded. Between-gen-2 counts exclude both endpoint
events and describe every complete interval in the corresponding workers.

| Profile | Preparation | Gen-0 | Gen-1 | Gen-2 | Gen-0 / gen-1 between adjacent gen-2 |
|---|---|---:|---:|---:|---|
| autoload | No | 2200 | 200 | 6 | 418 / 38 |
| autoload | Yes | 2202 | 200 | 18 | 121 / 11 |
| django_only | No | 2201 | 201 | 19 | 121 / 11 |
| django_only | Yes | 2202 | 200 | 18 | 121 / 11 |

The zero-based read indices containing gen-2 were:

- Autoload without preparation: `0, 76, 152, 228, 304, 380`.
- Django-only without preparation: `2, 23, 45, 67, 89, 111, 133, 155, 177,
  199, 221, 243, 265, 287, 309, 331, 353, 375, 397`.
- Both prepared profiles: `21, 43, 65, 87, 109, 131, 153, 175, 197, 219,
  241, 263, 285, 307, 329, 351, 373, 395`.

All these gen-2 events occurred inside `loads` intervals. Young-generation
event counts are nearly identical, while the unprepared autoload profile has
a substantially longer recurring interval before gen-2. The django-only
19-versus-18 difference is consistent with its shifted initial position and
the finite read-loop boundary, rather than a different recurring event cadence.

### Collected objects and limits

Without preparation, the first automatic gen-2 reported `collected=59`; every
later gen-2 reported zero. With preparation, the explicit collection reported
59 and every automatic gen-2 reported zero. Across all workers there were 488
automatic gen-2 events, including 472 with `collected=0`. All recorded events
had `uncollectable=0`.

The extra full collections are therefore not accompanied by repeated discovery
of collectible garbage in this loop. Matching counts of 59 in separate workers
do not establish object identity or provenance. Zero uncollectable objects
does not establish that no objects remain reachable through unwanted references.

The experiment localizes the difference to selection of full collections after
similar young-generation work. A one-step difference in the initial third
counter does not by itself explain the recurring 38-versus-11 gen-1 intervals.
The reports do not record all internal state used to choose gen-2; they cannot
identify one particular CPython heuristic as the cause. This diagnostic stage
is complete, while that mechanism remains an open research question. No
production change to routing hints, serialization or GC policy follows from
these observations.

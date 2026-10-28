# Development and verification

Run commands from the repository root. Start from the committed dependency set:

```bash
uv sync --locked --all-extras --dev
```

## Local checks

```bash
uv run --locked pyright src/nova \
&& uv run --locked ruff check . \
&& uv run --locked ruff format --check . \
&& uv run --locked pytest -q -rs \
&& git diff --check
```

The default Django settings use SQLite. Tests requiring PostgreSQL or explicitly
configured remote services may skip. `-rs` prints their reasons; the existence of
a running container alone does not select an integration fixture.

Pyright respects the repository's exclusions and exceptions. A clean run is not
a claim that every module is checked with unrestricted strict typing.

## Full service run and coverage

The development checkout uses PostgreSQL on `55432`, Redis on `56379`, and
Memcached on `51211`. The following command needs both compose files and the
development dependencies, including `psycopg`, `redis`, and `pymemcache`:

```bash
docker compose -p nova-orm-tests \
  -f compose.test.yaml -f compose.memcached.test.yaml \
  up -d --wait postgres redis memcached \
&& NOVA_TEST_REDIS_URL=redis://127.0.0.1:56379/0 \
  NOVA_TEST_MEMCACHED_SERVER=127.0.0.1:51211 \
  uv run --locked pytest -q -rs \
  --ds=tests.example_project.settings_postgres \
  -W error::pytest.PytestWarning \
  --tb=short \
  --cov=src/nova \
  --cov-branch \
  --cov-report=term-missing \
  --cov-report=xml:coverage.xml \
&& git diff --check
```

Use the disposable test services for this command. Prefixing a command with
environment variables sets them only for that process. Repeat the prefixes for
the next pytest invocation, or export them deliberately in that shell.

If a service or optional dependency is missing, inspect `-rs` before describing
the run as complete. An explicitly configured but unavailable integration service
should be fixed rather than counted as successful coverage. A coverage percentage
does not reveal the number or reason of skipped tests.

After the run, stop the test services:

```bash
docker compose -p nova-orm-tests \
  -f compose.test.yaml -f compose.memcached.test.yaml down
```

## Refresh status from the same snapshot

After the coverage command succeeds, use that XML to update both generated files:

```bash
uv run --locked python scripts/generate_status.py --write \
&& uv run --locked python scripts/generate_status.py --check \
&& uv run --locked python scripts/update_badge.py --write \
&& uv run --locked python scripts/update_badge.py --check
```

`STATUS.md` and the badge use XML line coverage. Coverage.py's terminal combined
line-and-branch figure may differ. `Miss` counts unexecuted statements, `Branch`
counts branch destinations, and `BrPart` counts partially covered branches;
these are not skipped tests. See [coverage.py's branch coverage explanation](https://coverage.readthedocs.io/en/latest/branch.html).

Keep the command, Git revision, dependency versions, and XML with the result.
The status script verifies correspondence to the supplied XML and source
inventory, not source-content freshness. Run coverage again after source changes.

## Demo and documentation

```bash
uv run --locked python -m examples.dogfooding \
&& uv run --locked pytest -q tests/docs/test_dogfooding_demo.py \
&& uv run --locked --with-requirements docs/requirements.txt mkdocs build --strict
```

Documentation dependencies are pinned separately from application dependencies.
`--with-requirements` supplies them for this invocation. MkDocs checks the site
navigation, local page links, and included example source. Strict mode fails on
warnings. For a local preview:

```bash
uv run --locked --with-requirements docs/requirements.txt mkdocs serve
```

See [MkDocs configuration](https://www.mkdocs.org/user-guide/configuration/) and
[uv script dependencies](https://docs.astral.sh/uv/guides/scripts/) for these options.

The documentation workflow builds on pull requests and publishes after a push
to `main`. On GitHub, open **Actions → Docs & Status → the run → build** and
expand **Build MkDocs** and **Deploy to GitHub Pages**. A skipped deploy on a pull
request is expected. Confirm the published URL under **Settings → Pages** after
merging; a successful site build alone does not prove a successful publication.

## Benchmarks and scripts

The default test path is `tests`; workload benchmarks run explicitly:

```bash
NOVA_TEST_REDIS_URL=redis://127.0.0.1:56379/0 \
NOVA_TEST_MEMCACHED_SERVER=127.0.0.1:51211 \
NOVA_MIX_OUTPUT=/tmp/nova-cache-workload.json \
uv run --locked pytest -q -s \
  --ds=tests.example_project.settings_postgres \
  -W error::pytest.PytestWarning --tb=short \
  benchmarks/queryset_cache_workload.py
```

Do not add coverage to latency measurements. Keep timing, full diagnostics, and
GC-only diagnostics in separate processes with the same workload and GC settings.
Report actual hit rate after invalidation and writes per read count, not an
invented requests-per-second workload.

For ordinary scripts, linting and tests serve different purposes. `pytest scripts`
only collects matching test modules; it does not execute every script as a test.
Run script regression tests from `tests`, and use explicit commands for scripts
that generate status, reports, or benchmark results.

### Isolated serializer and GC experiment

Use this experiment to distinguish allocations during deserialization from those
during serialization and result validation. It calls Nova's native
`PickleSerializer` directly. PostgreSQL prepares the same fixture data used by
the cache benchmarks; no cache backend or network client is created. All SQL,
including lazy relation access, is rejected after preparation.

First check the experiment's behavioral contracts:

```bash
uv run --locked pytest -q tests/benchmarks/test_serializer_gc.py \
&& uv run --locked ruff check \
  benchmarks/serializer_gc.py scripts/run_serializer_gc.py \
  tests/benchmarks/test_serializer_gc.py \
&& uv run --locked ruff format --check \
  benchmarks/serializer_gc.py scripts/run_serializer_gc.py \
  tests/benchmarks/test_serializer_gc.py \
&& git diff --check
```

Run the PostgreSQL matrix without coverage, a debugger, profiling or tracemalloc:

```bash
docker compose -p nova-orm-tests -f compose.test.yaml \
  up -d --wait postgres \
&& uv run --locked python scripts/run_serializer_gc.py \
  --settings tests.example_project.settings_postgres \
  --rows 100 --operations 400 --warmups 5 --repeats 4 \
  --output-dir /tmp/nova-serializer-gc
```

The output directory must be new. Choose a different name for subsequent runs;
the launcher never overwrites results. It launches 64 sequential processes:
2 operations × 2 model graphs × validation on/off × 2 experiment modes ×
4 repeats. Execution order is shuffled with a recorded seed. Each process runs
one variant and one repeat, so results from earlier variants cannot accumulate
in that interpreter. Each worker prepares a disposable test database; do not run
another test process against that same database concurrently.

| Dimension | Variants and meaning |
| --- | --- |
| Operation | `loads`: restore the same prebuilt bytes each time; `dumps`: serialize the same already materialized model list each time |
| Model graph | `plain`, `prefetch_related("tags")`; both use the same row counts and fixture values |
| Validation | `loads`: project IDs, titles and loaded tag names; `dumps`: compare bytes with the reference payload, without calling `loads` |
| Mode | `timing`: natural GC and primitive observations; `lifetime`: weak references and explicit `gc.collect(2)`, with no call-latency samples |

Validation-off skips **per-operation** validation. Timing workers still perform
one untimed correctness preflight, then the configured warmups. Loaded results
are deleted after every call; serialized byte results are also deleted. A
`dumps` worker retains its input model graph throughout the loop, as required for
repeated serialization, and checks it again after timing.

Call latency includes only the native `loads` or `dumps` call. Preparation,
validation and result disposal are outside that interval. Validation has its own
timestamps and can influence subsequent natural GC. The GC callback observes
the entire loop, including disposal and gaps between calls. Reports retain every
collection's generation, duration and collected/uncollectable counts, not only
collections intersecting slow calls. Preparation and warmup have separate GC
scopes. These are observed timings; the callback and primitive sample records
still have an allocation cost.

The terminal prints each worker's call mean/p95, validation mean, whole observed
loop duration and collection counts for generations 0/1/2. Validation means are
reported separately from call means. Whole-loop duration includes validation,
disposal and observation overhead; it is not pure serializer time. The manifest
also retains the complete per-generation object counts for each repeat.

Timing mode never calls `gc.collect`, changes thresholds, freezes GC or builds
weak-reference graphs. Lifetime mode runs in its own processes, records live
models and cached relation QuerySets before and after dropping results, and
performs explicit collections. With validation enabled, it keeps the primitive
validation projection alive during the first release check. These lifetime
results distinguish collectible graphs from continued retention at that
checkpoint; they are not latency measurements or proof against every leak.

The output contains `manifest.json` with per-repeat summaries, a JSON report and
a log for every worker. Check `complete: true` in both the manifest and worker
reports. Failure stops the launcher and preserves an incomplete manifest with
the relevant log. The launcher verifies matching source hashes, environment and
GC policy across workers. Keep the entire output directory with the Git revision.

Compare call means, medians, p95/p99 and the four within-process blocks **per
repeat**. Also compare total GC collections and collected objects, not only GC
overlap with call latency. `validation_overlap_ns` is part of
`outside_operation_ns`; do not add them as disjoint durations. GC counts are
process-wide, and collected objects include interpreter/Django temporaries, not
only the weakly observed models. Sparse gen-2 observations need more operations
or repeats before drawing conclusions.

Filter the matrix with `--mode timing`, `--operation loads`, `--scenario plain`
or `--validation off`. To check the launcher quickly without PostgreSQL:

```bash
uv run --locked python scripts/run_serializer_gc.py \
  --settings tests.example_project.settings \
  --rows 2 --operations 12 --warmups 1 --repeats 1 \
  --output-dir /tmp/nova-serializer-smoke
```

This SQLite run checks execution and report structure. Use the full PostgreSQL
matrix on the original measurement host for performance comparisons.

### Controlled instance-hint experiment

The opt-in `--hint-comparison` matrix isolates the back-reference from a cached
tag QuerySet's `_hints["instance"]` to its parent Article. It requires
`--operation loads --scenario prefetch_related`; it does not change the original
64-worker serializer matrix unless explicitly selected.

Both arms perform identical preparation: materialize two independent graphs
from the same database rows, preserve the original, and remove only the instance
hint from the control graph's cached tag QuerySets. The original payload is
checked for byte equality after this edit. All concrete Article/Tag fields,
database aliases, model state and ordered loaded tag memberships must match,
including after both payloads are deserialized. Unexpected or shared hint
structures fail the experiment. Public `tags.all()` reads and every serializer
round trip run under a SQL rejection guard after database materialization.

Preparation returns only the chosen bytes, primitive expectations and proof
metadata. Both graphs and temporary QuerySets leave scope before warmup. No
explicit collection, weak-reference graph, serializer wrapper or GC policy
change is introduced in timing workers. Preparation and warmup collections are
reported separately from the loop. They can affect the natural collector's
starting state; inspect all scopes rather than treating preparation as absent.

Check the experiment before the diagnostic run:

```bash
uv run --locked pytest -q tests/benchmarks \
&& uv run --locked ruff check . \
&& uv run --locked ruff format --check \
  benchmarks/serializer_gc.py benchmarks/serializer_hint_graph.py \
  scripts/run_serializer_gc.py scripts/summarize_serializer_gc.py \
  tests/benchmarks/test_serializer_hint_gc.py \
&& git diff --check
```

Use a new persistent result directory on the original PostgreSQL measurement
host. Redis and Memcached are not used by this isolated serializer experiment:

```bash
set -o pipefail
report_dir="$HOME/nova-reports/serializer-hints-$(date +%Y%m%d-%H%M%S)"
docker compose -p nova-orm-tests -f compose.test.yaml up -d --wait postgres \
&& uv run --locked python scripts/run_serializer_gc.py \
  --settings tests.example_project.settings_postgres \
  --operation loads --scenario prefetch_related --hint-comparison \
  --rows 100 --operations 400 --warmups 5 --repeats 4 \
  --output-dir "$report_dir" \
&& uv run --locked python scripts/summarize_serializer_gc.py \
  "$report_dir" --slow-ms 20 --json "$report_dir/analysis.json" \
  | tee "$report_dir/analysis.txt"
```

This launches 32 sequential fresh processes: original/control × validation
off/on × timing/lifetime × four repeats. The analyzer requires complete pairs
and identical semantic proof for each pair. Old reports without a hint variant
remain supported. Use `--settings tests.example_project.settings` for SQLite
verification, not as a substitute for the PostgreSQL measurement.

Compare per repeat, with validation modes kept separate:

- All generation 0/1/2 counts, collected/uncollectable objects and GC durations,
  including events outside slow calls. With equal operation counts, compare
  gen-2 collections per 400 operations, not just raw counts across unequal runs.
- `loads` mean/p99 and calls over 20 ms, plus their gen-2 overlap. A small number
  of pauses can disappear from p99; retain slow counts and the raw intervals.
- The additional `loads+validation+disposal` interval and entire loop duration.
  Breaking a cycle can move deallocation cost into reference-counted disposal
  immediately after `del result`. Faster `loads` alone would miss that cost.
  GC's `other` bucket includes disposal and observer work; it is not added to
  the call/validation buckets a second time.
- Lifetime graph nodes, cyclic components, downstream models and weakref
  survival before/after the separate explicit collection. Equal model values
  alone would not establish that object ownership is comparable.

Lifetime and timing are separate processes. Graph edges are a projection of
known ORM caches, not the complete interpreter reference graph; collection
counts are process-wide. A control that loses the observed cycles and reduces
GC pauses supports a contribution from this edge under this workload, without
proving that all allocations or all production GC delays come from it.

**Diagnostic only:** Django uses instance hints for database routing. Equal
already-loaded reads do not establish safety for routing, cache misses, writes
or arbitrary related managers. No production serializer/cache code is changed.

## Serializer process environment

The next isolated comparison runs identical prefetched payloads and measurement
code in adjacent minimal Django and pytest processes, reversing their order by
repeat. See [Serializer process environment](serializer-environment.md) for the
matrix, previous-payload verification and empty generation-2 pause analysis.

## GC object inventories

Inspect startup and post-warmup heap composition in separate terminal diagnostic
workers. See [GC object inventories](gc-inventory.md) for generation/type tables,
observer boundaries, reference validation and the 40-process comparison.

## Pytest plugin profiles

Compare the current autoloaded third-party plugins with explicit pytest-django
in adjacent fresh processes. See [Pytest plugin profiles](pytest-plugin-profile.md)
for verified plugin registration, separate timing/inventory workers and the
72-process matrix using the preceding experiment's fixed payloads.

## Explicit full-GC probes

Separate the duration of one full collection from automatic collection frequency.
See [Explicit full-GC probes](full-gc-probe.md) for the 16-process comparison,
untimed preparation, measured `gc.collect(2)`, GC state restoration and separate
empty/nonempty summaries. The existing `loads()` latency experiment is unchanged.

## Preparatory collection and automatic GC

Compare the same automatic read loop after warmup with no explicit collection
or one `gc.collect(2)`. See [Preparatory collection](gc-preparation.md) for
adjacent pairs within each profile, the 32-process matrix, full GC accounting
and zero-based event positions. Preparation stays outside read latency.

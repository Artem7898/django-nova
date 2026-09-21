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

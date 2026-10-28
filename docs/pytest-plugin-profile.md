# Serializer GC: pytest plugin profiles

This experiment compares two pytest environments with the same fixed payloads,
installed package versions, Django settings, serializer, warmup and timing loop:

- `autoload`: the installed third-party plugins from the preceding environment
  reference, loaded normally. Their names and versions must match that reference.
- `django_only`: automatic third-party plugin loading is disabled, and
  `pytest-django` is explicitly loaded with `-p django`. Pytest's built-in plugins
  remain enabled. Packages are not uninstalled and dependency versions do not change.

It changes no production code and does not modify the previous serializer or
inventory experiment. The new timing adapter calls the existing
`serializer_environment.timed_reads()` directly. The comparison isolates the
third-party plugin set as a group, not the cost of one specific plugin.

## Why a separate launcher and configuration

The preceding launcher deliberately removes inherited
`PYTEST_DISABLE_PLUGIN_AUTOLOAD`, `PYTEST_PLUGINS` and coverage overrides. The new
launcher first applies the same cleanup, then sets
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` for the `django_only` child. Setting this only
in the parent shell would not implement the intended experiment.

Both profiles use `pytest.plugin-profile.ini`, preserving the project's Python
paths, Django setup and marker declarations. Its common options do not refer to
unloaded third-party plugins. The autoload profile additionally receives
`-o asyncio_mode=auto`, matching the original asyncio configuration. Without
pytest-asyncio that option is irrelevant and is not passed. The diagnostic test
is synchronous. This avoids hiding unknown-option warnings or changing the main
project's pytest configuration.

Neither command passes `--no-cov`, because the django-only profile does not load
the plugin that defines it. Instead, inherited coverage options are removed,
`addopts` is empty, and both workers reject active coverage, tracing, profiling,
tracemalloc and optimized Python before and after work. `PytestWarning` remains
an error.

Every worker records command arguments, config file, root directory, distribution
names/versions, registered plugin names/modules and effective autoload/asyncio
settings. The django-only worker accepts only pytest built-ins and the Django
plugin; a registration absent from distribution metadata is still checked. Class
plugins are identified by their declared module, not `type(plugin).__module__`.
Registrations and configuration must remain identical before and after work.

## Process matrix and boundaries

Default four repeats produce 72 sequential fresh workers:

| Work | Cells | Profiles | Repeats | Processes |
|---|---:|---:|---:|---:|
| Timing: original/control × validation off/on | 4 | 2 | 4 | 32 |
| Inventory after warmup: same four cells | 4 | 2 | 4 | 32 |
| Inventory after startup | 1 | 2 | 4 | 8 |

Within each pair the workers are adjacent. Odd repeats run autoload then
django-only; even repeats reverse the order. Cell order is seeded. Row count,
operation count, warmup count and Django settings come from the completed
serializer-environment reference. A typical reference uses 100 rows, 400 timed
operations and five warmups after the always-validated warmup read.

Timing workers never import the inventory module or enumerate GC objects. They
preserve the existing load/optional-validation/delete boundaries and normal GC
policy. GC callbacks cover preflight, warmup and the measured loop, including
collections outside slow operations. No explicit collection, threshold change,
weak-reference graph, serializer/backend phase wrapper or GC suspension is
introduced into timing.

Inventory workers take one terminal snapshot in a different process. Startup
means Django and harness setup have finished, before restoring a payload.
After-warmup includes preflight and the same semantic warmup schedule. They reuse
the existing [inventory observer](gc-inventory.md): GC is suspended only during
terminal enumeration and restored in `finally`, without `gc.collect()` or
freeze/unfreeze. Known observer containers and pre-existing frozen objects are
reported separately. There is no timed loop in inventory workers. SQL remains
forbidden during input validation, warmup, timing and inventory inspection.

The new adapter, plugin evidence and common configuration add harness state to
both profiles. The autoload arm is therefore a new matched baseline, not a claim
of byte-identical process state to an older run. Compare adjacent new pairs;
do not attribute a difference from old latencies solely to plugin removal.

## Install and verify

Install after the serializer-environment and GC-inventory experiments. Keep the
interpreter, `uv.lock` and previous source files unchanged.

```bash
uv run --locked pytest -q tests/benchmarks \
&& uv run --locked pyright src/nova \
&& uv run --locked ruff check . \
&& uv run --locked ruff format --check \
  benchmarks/pytest_plugin_profile.py benchmarks/pytest_plugin_profile_test.py \
  scripts/run_pytest_plugin_profile.py scripts/summarize_pytest_plugin_profile.py \
  tests/benchmarks/test_pytest_plugin_profile.py \
&& git diff --check
```

Tests launch a small paired matrix, inspect actual registered plugins, verify
timing/inventory separation, preserve collector restoration and SQL guards, and
reject altered inputs, source/version differences, configuration drift and
incomplete results. They assert no particular speedup or GC count.

## Run on the measurement host

Use the completed **serializer-environment directory**, not the GC inventory
directory. Its fixed trusted payloads are copied unchanged; no new database
fixture is exported. PostgreSQL, Redis and Memcached containers are not required.

```bash
set -o pipefail
reference_dir="$HOME/nova-reports/serializer-environment-20261025-032437"
report_dir="$HOME/nova-reports/pytest-plugins-$(date +%Y%m%d-%H%M%S)"

uv run --locked python scripts/run_pytest_plugin_profile.py \
  --reference-dir "$reference_dir" --output-dir "$report_dir" --repeats 4 \
&& uv run --locked python scripts/summarize_pytest_plugin_profile.py \
  "$report_dir" --slow-ms 20 --json "$report_dir/analysis.json" \
  | tee "$report_dir/analysis.txt"
```

Run without concurrent tests or benchmarks. Existing output directories and
analysis files are not overwritten. On failure, inspect the indicated child log;
the manifest remains incomplete. The analyzer checks all pairs, configurations,
process identities, sequential launches, input/report hashes and callback totals
against collector statistics. It never unpickles data. Worker unpickling is only
for the trusted payloads produced by the previous local experiment.

## Interpret the comparison

Keep original/control and validation settings separate, and inspect every pair:

- For timing, compare gen-2 `collected_zero` count and mean/median/p95/max/total
  duration. Empty buckets retain null estimates, not a zero-cost claim. Also
  compare nonempty gen-2 and all generations in preparation, warmup and the loop.
- Compare `loads` mean/p99 and `loads+validation+disposal` mean/p99, slow counts,
  slow/GC overlap and whole-loop duration. A longer pause can coexist with fewer
  pauses; both frequency and duration matter to total cost.
- `django_only_over_autoload` ratios below one mean a lower measured duration
  for that adjacent pair. They are not confidence intervals or significance tests.
- For inventory, inspect full type/module tables in the worker reports and
  `autoload_minus_django_only` deltas in analysis JSON. Startup/warmup differences
  use separate processes and cannot establish individual-object retention.
- Fewer tracked objects together with shorter pauses would support a contribution
  from the removed plugin set under this workload. It would not identify an
  individual plugin, establish a universal count-to-latency relationship, or
  justify modifying production routing hints or GC settings.

Preserve the complete output directory for follow-up analysis. Installed package
versions are intentionally identical in both arms; imported runtime state and
registered third-party plugins are the experimental difference.

# Project status

[STATUS.md](https://github.com/Artem7898/django-nova/blob/main/STATUS.md) is generated
from a coverage XML snapshot. The README badge and STATUS summary use line
coverage. The terminal report with branch coverage combines lines and branches,
so its percentage can differ.

Coverage is evidence of executed code, not proof of correctness or API stability.
`Miss` counts unexecuted statements, `BrPart` counts partially covered branches,
and pytest's `skipped` count refers to test cases. A full service run and a local
SQLite run may execute different sets of tests.

Use the [verification guide](testing.md) to refresh the report and interpret skips.
Keep the source revision, command, dependency versions, and coverage XML together.

## Completed stages

- Documentation and the reproducible dogfooding demo: executable demo tests and
  a successful strict documentation build.
- [Context semantics](context.md): nested restoration, async isolation,
  exceptions, cancellation, and logging failure contracts. The focused suite
  measures 100% combined line-and-branch coverage for `nova.core.context`, above
  the 85% target. This is a module-level result, not whole-project coverage.
- [DRF adapter](drf.md): real serializer/ORM creation and updates, partial updates,
  defaults, field rules, foreign keys, uploads, validation errors, and viewset
  integration. The focused SQLite suite measures 94% combined line-and-branch
  coverage, above the 85% target; the full service run confirms 94%.
- Full local PostgreSQL + Redis + Memcached checkpoint: 2072 passed, no skips,
  88% combined coverage. Pyright, Ruff, formatting, and strict docs build pass.
  This is a local checkpoint; required GitHub CI must pass before release.

## Remaining work order

1. Investigate temporary objects on the serialization path after comparing full
   instrumentation with GC-only observation.
2. Confirm GraphQL API stability before expanding runtime integration tests.

Update CHANGELOG as each stage closes. Keep new work under `Unreleased` until an
actual release includes it. The targets for Context and DRF complement behavioral
checks; they do not replace them.

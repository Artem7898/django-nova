# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

## [0.6.3] - 2026-09-21

### Overview

Django Nova is a production-oriented Django infrastructure toolkit for typed
models, schema-driven validation, query planning, caching, and observability.
Development status remains **Beta**. This release strengthens tested runtime
contracts without declaring the whole public API production-stable.

### Cache correctness and isolation

- Coordinate QuerySet cache invalidation across processes through shared Redis
  and Memcached generations scoped by model and database alias. Rotate after a
  successful commit, including when the writer has never populated a query.
- Keep overlapping fills associated with their captured generation so that late
  writes cannot publish old results into a newer generation.
- Add regressions for metadata eviction, backend failures, and replayed metadata
  commands. Known generation transports avoid retrying a metadata write after
  it may already have been applied.
- Track supported related-model dependencies and test their invalidation across
  processes, alongside query-key and transactional read-consistency boundaries.
- Isolate returned lists, models, JSON values, and loaded relations. Retain
  conservative snapshotting for memory and unknown backend implementations.
- Batch generation reads. Avoid redundant snapshots only when a backend exposes
  the appropriate explicit guarantee: detached reads and independent storage on
  writes are separate contracts. Preserve ORM results on the supported cache
  failure paths.

### Backend contracts and diagnostics

- Expand synchronous/asynchronous Redis contracts, Memcached contracts, and real
  backend integration tests, including TTL boundaries, expiry, namespace
  isolation, connection behavior, and failures.
- Add cost and mixed-workload benchmarks, GC diagnostics, and object-lifetime
  checks. Performance results describe their measured workload and environment;
  the next diagnostic stage isolates instrumentation overhead.

### Context and DRF

- Restore nested context scopes with ContextVar tokens and verify async task
  isolation, inheritance, exceptions, cancellation, and logging failure behavior.
- Validate projected scalar Django defaults once on serializer creation and keep
  omitted update fields unchanged.
- Validate foreign keys against their referenced scalar values, including
  to_field, while preserving nested schema handling and persistence objects.
- Validate uploads by name while retaining file contents for storage; respect
  DRF's configured non-field error key.
- Expand serializer, ORM, and viewset contracts for create, full/partial updates,
  defaults, field rules, relationships, files, and save-time validation. Include
  the DRF module in both Pyright configurations.

### Documentation and compatibility

- Complete the executable dogfooding demo, API navigation, and guides for
  validation, caching, tasks, tracing, context, and DRF boundaries.
- Keep Development Status :: 4 - Beta. API stability and deployment suitability
  remain separate from test counts and coverage.
- On TTL-capable backends, non-positive TTL expires/removes the value; use
  ttl=None for persistent storage. Backends that explicitly do not support TTL
  retain their documented behavior.
- Shared generations do not create an atomic transaction between PostgreSQL and
  the cache. Custom transports and storage implementations must meet the stated
  generation and isolation contracts; arbitrary direct/bulk SQL writes do not
  automatically gain signal-based invalidation.
- Nested/M2M serializer writes, relationship defaults, save-time HTTP error
  translation, and batch save atomicity still require explicit application
  handling. GraphQL runtime expansion awaits confirmation of API stability.

### Verified development checkpoint

- Full local PostgreSQL + Redis + Memcached run: **2072 passed, no skips**.
- Combined line-and-branch coverage: **88%**; line coverage approximately **90.45%**.
- Context: **100%** combined coverage; DRF adapter: **94%** combined coverage.
- Python 3.12.13; Pyright: **0 errors, 0 warnings**; Ruff lint and formatting pass.
- Strict documentation build succeeds. These figures describe the supplied
  pre-release checkpoint; refresh generated reports after release preparation.

Full changes: https://github.com/Artem7898/django-nova/compare/v0.6.2...v0.6.3

### DRF adapter runtime contracts

- Validate projected scalar Django defaults on creation and reuse the same
  evaluated values during save. Keep omitted update fields unchanged.
- Adapt foreign-key instances to their referenced values for scalar Pydantic
  fields, including `to_field` references. Preserve nested/optional nested schema
  validation and the original Django objects used for persistence.
- Validate uploaded files by name while retaining the uploaded file for storage.
- Honor DRF's configured `NON_FIELD_ERRORS_KEY` when translating Pydantic errors
  and report invalid schema configuration with a clear `ValueError`.
- Add serializer/ORM regressions for create, full/partial update, defaults,
  required/read-only/nullable/JSON fields, uniqueness, cross-field rules,
  invalid-state repair, save-time validation, list validation, relationships,
  and file contents. Verify generated serializers through viewset POST/PATCH.
- Include the DRF adapter in both Pyright configurations after checking its
  dynamic Django/DRF metadata boundaries.
- Document supported behavior and explicit boundaries for nested/M2M writes,
  normalization, relationship defaults, save-time errors, and batch atomicity.
- Raise measured DRF line-and-branch coverage from 63% to 94% in the focused
  SQLite run; the subsequent full PostgreSQL/Redis/Memcached run also passed.

### Context semantics

- Add behavioral tests for nested and empty context scopes, shallow binding
  snapshots, exceptions, async sibling isolation, task inheritance, cancellation
  and subsequent recovery, and explicit thread propagation boundaries.
- Restore `new_context()` using the original `ContextVar` token. Preserve
  replacement semantics, sync and async body behavior, and the existing
  best-effort structlog bridge.
- Verify that missing structlog and ordinary logging failures preserve Nova
  context behavior and business exceptions, including cancellation.
- Document mutable-value sharing, the synchronous decorator limitation, direct
  structlog binding ownership, and explicit propagation across queue/process
  boundaries. These are boundaries of the existing API, not new propagation
  guarantees.
- Measure 100% combined line-and-branch coverage for `nova.core.context` in the
  focused suite, exceeding the stage's 85% target; full service integration
  subsequently passed.

### Documentation and dogfooding demo

- Add `python -m examples.dogfooding`: an isolated Django app with a committed
  migration, in-memory SQLite and file storage, and executable checks for schema
  selection, unsaved M2M safety, generated timestamps, file names, Decimal
  normalization, validation strictness, public exports, and task submission.
- Add subprocess regressions for the documented command and migration/model
  consistency. Include the executable model source directly in the demo guide.
- Complete installation, architecture, validation, caching, task, tracing,
  migration, and verification guides; repair navigation and local links.
- Correct Python/Django requirements and distinguish local SQLite runs from
  PostgreSQL/Redis/Memcached integration, skipped tests from uncovered code, and
  line coverage from combined line-and-branch coverage.
- Pin the documentation tools separately and build the documentation in strict
  mode in the documentation workflow.
- Correct the database router configuration example and migration helper
  descriptions. SQL generation and routing behavior are unchanged.
- Track remaining serialization/GC diagnostics and conditional GraphQL work
  separately from the completed documentation, Context, and DRF stages.

## [0.6.2]

### Fixed

- Defer signal-driven cache invalidation until the corresponding database
  transaction commits. Rollbacks discard pending invalidation callbacks.
- Prevent an overlapping QuerySet cache fill from republishing stale results
  after invalidation or cache clearing within the same shared process-local state.
- Escape literal Redis key prefixes during synchronous and asynchronous cache
  clearing, preventing glob characters from matching neighboring namespaces.
- Preserve TypedField ORM and migration behavior, verified through database
  write/read round trips and migration application and reversal.

### Added

- Transactional cache invalidation tests covering commit, rollback, savepoints,
  database aliases, repeated signal registration, and backend failures.
- PostgreSQL concurrency regressions for cache fills completing before and
  after a committed write.
- Synchronous Redis backend contracts and asynchronous regression tests for
  expiration, bulk operations, error propagation, and task cancellation.
- Real Redis integration tests for serialization, TTL, persistent overwrites,
  namespace isolation, and clearing large sets of keys.
- SQLite and PostgreSQL integration coverage for TypedField ORM operations
  and migrations.
- Disposable PostgreSQL and Redis services for local integration testing.

### Changed

- Configure CI to run the full suite with PostgreSQL and Redis.
- Include Pyright and Ruff checks in the test workflow.

### Compatibility and scope

- Transactional invalidation occurs after commit rather than immediately
  during model save or deletion.
- The QuerySet cache generation guard coordinates instances sharing the same
  process-local state; it does not provide distributed cache coherence.
- Redis size metrics retain their existing behavior.

### Verification

- Full local PostgreSQL and Redis run: 951 tests passed, with no skips.
- Pyright: 0 errors and 0 warnings under the repository configuration.
- Ruff and git diff --check passed.
- Coverage percentages are reported separately from measured coverage data.
[0.6.2]: https://github.com/Artem7898/django-nova/compare/v0.6.1...v0.6.2


## [0.6.1]

Historical dogfooding and validation improvements included in v0.6.1.
Verification figures below describe the development checkpoint for that release.

Repository dogfooding, validation contracts, and runtime reliability improvements.
These changes describe the reviewed development work; they do not assign a new
release version or imply that the changes are already available on PyPI.

### Fixed

- Completed field-type mapping for the tested scalar fields, including Decimal
  and UUID; use MRO lookup for inherited fields and `Any` for unknown field types.
- Prevented callable Django defaults from being evaluated during Pydantic schema
  generation. Factories now run for each Pydantic instance when the value is omitted.
- Transferred Decimal `max_digits` and `decimal_places` to generated schemas,
  including zero decimal places. Restricted length constraints to supported text
  fields so UUID values do not receive a `max_length` validator.
- Made canonical model serialization respect the selected schema before reading
  attributes. File fields serialize as names/paths; unrelated M2M fields are not
  accessed on unsaved instances.
- Added nested foreign-key serialization for the tested async save scenario,
  replacing raw IDs with schema-selected related data where a nested schema is
  required. Recursive serialization has a depth limit.
- Fixed a runtime `NameError` in `pydantic_to_model()` caused by evaluating a
  type imported only under `TYPE_CHECKING` inside `cast()`.
- Restored `NovaManager` on `NovaModel` and `.auto()` on its querysets. Simplified
  queryset construction and kept database routing hints at a typed boundary.
- Preserved Django field conversion results on model attributes before
  `Model.clean()`, uniqueness checks, and constraint checks.
- Included automatic primary keys in concrete-field metadata while allowing an
  absent database-assigned primary key during validation of a new instance.
- Restored lazy public exports for `get_default_cache`, `NovaManager`,
  `TypedField`, and `TypedQuerySet` at the documented package entry points.
- Fixed all four tracing decorators to keep spans open across awaited coroutine
  execution and record exceptions raised after `await`.
- Isolated ordinary exceptions from internal telemetry setup, recording, status
  updates, and teardown. Preserve business results, original exceptions, and
  cancellation; ignore provider requests to suppress business exceptions.
- Fixed `TypedField` initialization and scalar conversion/validation delegation.
  Preserve constructor overrides without mutating the supplied inner field.
- Made `TypedField` migration descriptions reconstructible and clones independent;
  delegated database value preparation to the inner field.
- Corrected README badge updates to consume the replacement text and reject a
  missing or duplicated coverage badge.

### Added

- Regression contracts for schema whitelisting, file serialization, unsaved M2M
  safety, generated fields, callable defaults, and scalar field constraints.
- Tests proving that `strict_validation=False` skips Pydantic while retaining
  Django field validation and the subsequent model validation stages.
- Subprocess checks that importing public packages does not eagerly load Django
  or Pydantic, alongside public-export identity checks.
- Async tracing tests for span lifetime, exception recording, callable metadata,
  cancellation, and operation without OpenTelemetry.
- Telemetry failure-injection tests covering synchronous and asynchronous calls.
- Tests for `TypedField` scalar conversion, specialized validators, option
  overrides, cloning, migration-code serialization/reconstruction, and DB preparation.
- Moved 20 Django typing-boundary tests out of `tests/typing/__init__.py` into
  `tests/typing/test_django_boundary.py` so normal pytest collection includes them.

### Changed

- Reworked the status generator to distinguish missing measurements (`N/A`) from
  measured zero coverage. Overall line coverage uses XML root counters, while
  the source table includes package initializers and private modules.
- Removed coverage-derived production-readiness labels and hard-coded claims that
  modules have no tests. The generated report states its measurement scope and
  freshness limitations; malformed or missing XML fails generation.
- Reorganized the README around explicit Django/Pydantic responsibilities, working
  example structure, installation bounds, async behavior, and reproducible checks.
- Replaced unsupported zero-overhead and blanket lock-free migration claims with
  scoped descriptions and verification requirements.
- Updated the roadmap to separate verified behavior from outstanding work and
  refer to generated coverage measurements instead of hard-coded percentages.

### Compatibility and remaining scope

- `strict_validation=False` disables only the Pydantic stage; it does not disable
  Django validation or database constraints.
- Field validation now writes converted values back to the instance before
  `Model.clean()`. Code inspecting raw input at that stage must account for this.
- Nested foreign-key serialization can issue synchronous queries for unloaded
  relations. Nested M2M and reverse-relation support remain separate work.
- Telemetry protection covers Nova's internal calls, not application code calling
  span methods directly. Provider signals derived directly from `BaseException`
  are not suppressed.
- `TypedField` verification covers scalar behavior and reconstruction of migration
  code. Real ORM write/read round trips and migration application/rollback remain
  to be tested; relation, file-descriptor, and automatic timestamp integration are
  outside the verified wrapper contract.
- Test success and line coverage are not blanket claims of production readiness
  or unrestricted strict typing across every module.

### Verification

Latest full local run reported for this work:

- **802 tests passed** in **11.80 seconds**.
- **Pyright:** 0 errors, 0 warnings, 0 informations using the repository configuration.
- **Ruff:** all checks passed.
- The focused `TypedField` suite passed **23 tests**, including the existing tests.

Coverage generation and status `--write`/`--check` previously succeeded at the
786-test checkpoint. No coverage percentage is inferred from the later 802-test
run; refresh `coverage.xml` and `STATUS.md` after applying subsequent changes.

---

## [0.6.0] - 2026-09-05 - Type Safety Milestone

### Added
- **nova.typing.django** module (226 lines, 69% coverage)
- `safe_get_attname()` — safe access to .attname with GFK protection
- `get_model_pk()` — typed access to primary key
- `is_generic_foreign_key()` — type guard for virtual fields
- All imports are LAZY (no Django settings when importing)

### Fixed
- **nova.validation.unified** (162 lines, 97% coverage)
- GenericForeignKey guards in _validate_django_fields()
- Safe access to field.clean() and field.attname

- **nova.query.planner** (236 lines, 95% coverage)
- Replaced cast(DjangoField[...]) with getattr()
- Fixed TypeError: Field is not subscriptable

- **nova.core.tracing** (208 lines, 58% coverage)
- Fixed get_tracer(name) parameter
- Fixed issues with OTEL optional dependencies

- **nova.typing.models** (88 lines, 90% coverage)
- Used get_model_pk() for PK access
- Type-safe save() and __repr__()

### Metrics
- **Pyright**: 0 errors, 0 warnings (strict mode) ✅
- **Tests**: 408 passed (+205 from previous run!)
- **Coverage**: 66% (was ~56%) ⬆️
- **Modules**: 58 total (no change)

### Breaking Changes
None - fully backward compatible.

---

## [0.5.1] — 2026-06-27

### 🐛 Fixed

- **Critical:** Fixed `SchemaRegistry` caching logic. Previously, schemas were cached by metaclass (`type[model_cls]`), meaning the cache never hit. Now correctly cached by the model class itself.
- **Critical:** Fixed `to_dict()` and validation pipeline crash (`AttributeError`) when models with `ForeignKey` were iterated, due to unfiltered `ForeignObjectRel` in `_meta.get_fields()`.
- Fixed `TaskError` import mismatch in `tasks/__init__.py` public facade.
- Fixed `splitter.py` type signature preventing `pks=` kwargs from being passed to chunked migrations.

### 🔧 Changed

- **Type Safety:** Achieved `pyright --strict` **0 errors** across the entire codebase (`src/nova`).
- **Internal:** Canonicalized the "Safe Import" pattern for optional dependencies (OTEL, Redis, FastAPI, DRF, Strawberry) to completely isolate `Unknown` type cascading.
- **Internal:** Isolated OpenTelemetry tracing behind a strict `SpanLike` Protocol at the module boundary.
- Replaced deprecated `asyncio.iscoroutinefunction` with `inspect.iscoroutinefunction`.
- Enforced strict generic typing on internal data structures (`QueryPlan`, `_TaskPayload`, `TTLCache`).

### 🔒 Security

- Isolated optional package imports (e.g., `fastapi`, `strawberry`) to guarantee **zero runtime `ImportError`** for users who only install the core `django-nova` package.

---

## [0.5.0] — 2026-07-20

### 🐛 Fixed

- Corrected `django-modern-rest` description in the comparison table from "ORM toolkit" to "API framework". Thank you to Nikita Sobolev (wemake-services) for the correction.
- Removed Django Vanilla Views from the comparison table (irrelevant to the validation/API domain).

### 📊 Changed

- Added a comprehensive, fact-checked comparison table positioning django-nova against Django Modern REST, Django Ninja, and drf-pydantic.
- Updated benchmarks with real measurements: Pure Pydantic baseline (0.663 µs) vs NovaModel (1.818 µs), absolute overhead +1.155 µs per object.
- Rewrote the Roadmap into two honest sections: ✅ Already Shipped and 🚧 Future Work.
- Rewrote README sections: Problem Statement, Philosophy, Architecture, Installation, Quick Start, Schema Compiler, Smart Query Planner, Distributed Context, Infrastructure, Smart Cache, Zero-Downtime Migrations, Benchmarks.

---

## [0.4.0] — 2026-08-15

### ✨ Added

- Initial stable release.
- Typed ORM, Managers, and QuerySets with full `pyright --strict` compatibility.
- Pydantic Bridge & Unified Validation with bidirectional sync.
- Full Async ORM integration with native `AsyncTypedQuerySet` and `.aauto()` query planner.
- Auto DRF Serializer generation (`to_drf_serializer`).
- Auto Django Admin generation (`compile_admin`).
- Deep Query Planner with automatic field deferral.
- Unified Redis Client, Distributed Locks, Rate Limiter, and Pub/Sub facade.
- Lag-Aware Read Replica Router with automatic Master failover.
- Zero-Downtime Migrations via PostgreSQL `CONCURRENTLY`.
- OpenTelemetry Tracing, Structlog, and Distributed Context (correlation IDs).
- Stable Public API with PEP 562 Facades and Semver compliance.

---

## [0.2.0] - 2026-05-06 - "Enterprise & Ecosystem"

### Added
- **Structured Observability:** Integrated `structlog` for machine-readable JSON logging. Cache miss/hit events now include ISO-timestamps and execution timings.
- **Distributed Tracing:** Added OpenTelemetry integration (`nova.core.tracing`). Automatic spans for `Model.save()` and `QuerySetCache` operations. Uses "Safe Import" pattern (0 overhead if OTEL is not installed).
- **Migration Safety:** Implemented `AddFieldConcurrently` and `CreateIndexConcurrently` for true zero-downtime schema changes on PostgreSQL.
- **DRF Auto-Serializer:** Added `to_drf_serializer()`. Dynamically generates Django Rest Framework `ModelSerializer` that delegates business logic validation strictly to Pydantic schemas.
- **FastAPI Auto-Router:** Added `to_fastapi_router()`. Dynamically generates FastAPI endpoints (`GET/POST`) bound to Django ORM.
- **Native OpenAPI:** FastAPI routers automatically generate perfect Swagger/OpenAPI schemas using runtime signature injection (`inspect.Signature`), bypassing PEP 563 limitations.

[0.5.1]: https://github.com/Artem7898/django-nova/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/Artem7898/django-nova/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Artem7898/django-nova/releases/tag/v0.4.0

[0.6.3]: https://github.com/Artem7898/django-nova/compare/v0.6.2...v0.6.3

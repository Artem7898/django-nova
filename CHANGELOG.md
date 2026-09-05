## [0.2.0] - 2026-05-06 - "Enterprise & Ecosystem"

### Added
- **Structured Observability:** Integrated `structlog` for machine-readable JSON logging. Cache miss/hit events now include ISO-timestamps and execution timings.
- **Distributed Tracing:** Added OpenTelemetry integration (`nova.core.tracing`). Automatic spans for `Model.save()` and `QuerySetCache` operations. Uses "Safe Import" pattern (0 overhead if OTEL is not installed).
- **Migration Safety:** Implemented `AddFieldConcurrently` and `CreateIndexConcurrently` for true zero-downtime schema changes on PostgreSQL.
- **DRF Auto-Serializer:** Added `to_drf_serializer()`. Dynamically generates Django Rest Framework `ModelSerializer` that delegates business logic validation strictly to Pydantic schemas.
- **FastAPI Auto-Router:** Added `to_fastapi_router()`. Dynamically generates FastAPI endpoints (`GET/POST`) bound to Django ORM.
- **Native OpenAPI:** FastAPI routers automatically generate perfect Swagger/OpenAPI schemas using runtime signature injection (`inspect.Signature`), bypassing PEP 563 limitations.

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.5.1]: https://github.com/Artem7898/django-nova/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/Artem7898/django-nova/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Artem7898/django-nova/releases/tag/v0.4.0
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


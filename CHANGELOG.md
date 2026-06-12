## [0.2.0] - 2025-05-06 - "Enterprise & Ecosystem"

### Added
- **Structured Observability:** Integrated `structlog` for machine-readable JSON logging. Cache miss/hit events now include ISO-timestamps and execution timings.
- **Distributed Tracing:** Added OpenTelemetry integration (`nova.core.tracing`). Automatic spans for `Model.save()` and `QuerySetCache` operations. Uses "Safe Import" pattern (0 overhead if OTEL is not installed).
- **Migration Safety:** Implemented `AddFieldConcurrently` and `CreateIndexConcurrently` for true zero-downtime schema changes on PostgreSQL.
- **DRF Auto-Serializer:** Added `to_drf_serializer()`. Dynamically generates Django Rest Framework `ModelSerializer` that delegates business logic validation strictly to Pydantic schemas.
- **FastAPI Auto-Router:** Added `to_fastapi_router()`. Dynamically generates FastAPI endpoints (`GET/POST`) bound to Django ORM.
- **Native OpenAPI:** FastAPI routers automatically generate perfect Swagger/OpenAPI schemas using runtime signature injection (`inspect.Signature`), bypassing PEP 563 limitations.

### Changed
- `QuerySetCache` now uses SQL Compiler for deterministic cache key hashing (safe across all Django 5.x versions).
- `QuerySetCache` invalidation moved from hash-search to O(1) reverse index mapping.


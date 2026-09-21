# Architecture

Nova provides boundaries around Django rather than replacing the ORM.

| Component | Responsibility |
|---|---|
| `nova.typing` | Public models, managers, fields, and helpers around Django metadata. |
| `nova.validation` | Field compilation, schema selection, serialization, and save validation. |
| `nova.query` | Derive relation-loading and field-selection plans from supported schemas. |
| `nova.cache` | Cache supported query results and invalidate model/database scopes. |
| `nova.tasks` | Task submission and backend lifecycle. The default backend is process-local. |
| `nova.core` | Context values, tracing, configuration, and shared exceptions. |
| `nova.ecosystem` | Optional adapters whose runtime contracts require separate verification. |
| `nova.db` | Routing and migration helpers with database-specific requirements. |

## Model validation

`NovaModel.save()` runs Pydantic validation when enabled, Django field conversion
and validation, `Model.clean()`, uniqueness checks, and constraint checks before
Django saves the row. A failure stops the pipeline. Normalized field values are
assigned before `clean()` so a Decimal field can be used as a Decimal there.

An explicit Pydantic schema supplies its own rules. Generated schemas derive
supported scalar contracts from Django fields. Serialization selects the fields
in that schema. These operations do not replace database constraints or define
arbitrary relation serialization. See [validation](validation.md).

## Cached reads and committed writes

Supported query results have an identity that includes the chosen database,
query plan, and model generations. Readers capture generations before SQL;
writers rotate them after commit. A late fill keeps its original identity.
This permits old entries to become unreachable without finding every query key.

Remote coherence requires a backend that implements shared generations and
invalidation registration in each writer. Local memory only coordinates local
state. Transactions, unsupported query dependencies, missing metadata, and backend
failures require their defined fallback paths. See [caching](caching.md).

Returned mutable results must be independent. A backend's independent-read
capability and its independent-write capability are separate contracts; removing
one copy does not authorize removing the other.

## Async and optional integrations

An async API must be awaited. Task submission returns an identifier, while task
execution completes separately. Unloaded model relations may still cause
synchronous SQL. Context variables do not automatically propagate between
processes, and telemetry configuration belongs to the application.

See [tasks](tasks.md), [tracing](tracing.md), and [migration limits](migrations.md).
Performance and compatibility claims should identify the tested versions and
workload. The [verification guide](testing.md) separates local tests, service
integration, and diagnostics.

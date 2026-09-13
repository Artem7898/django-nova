# Django Nova Roadmap

This roadmap tracks verified work and remaining validation tasks. It does not
assign release maturity or production readiness from coverage percentages.

For coverage measurements, see [STATUS.md](STATUS.md), generated from
`coverage.xml`. A passing test suite and line coverage describe different aspects
of verification; neither establishes complete API coverage.

## Verification baseline

The latest full local run reported during this review completed with **720 tests
passed**, a clean Pyright result, and a clean Ruff result. This is a reported
snapshot, not a live CI badge or a guarantee for subsequent changes.

## Verified work

| Area | Confirmed behavior | Limits of this verification |
|---|---|---|
| Field compilation | MRO-based type lookup; fallback to `Any`; Decimal and UUID mappings; decimal digit constraints | Fallback to `Any` does not enforce a specialized field type. Full Django/Pydantic equivalence for all values is not established. |
| Field defaults | Callable defaults are evaluated per Pydantic instance when omitted; generated datetime fields may be omitted | Database-generated defaults and all primary-key configurations need separate contracts. |
| Scalar serialization | Schema whitelist; generated-schema exclusions; file names serialized as strings; unrelated M2M fields are not accessed | This does not establish nested M2M or reverse-relation support. |
| Nested foreign keys | Nested schema serialization supports the tested `asave()` scenario with an existing author supplied by ID | Unloaded relations may trigger synchronous SQL. Direct calls from async contexts require separate handling. |
| Validation configuration | `strict_validation=False` skips Pydantic while retaining Django validation; field errors stop later stages | Two lifecycle entry points remain to be compared before consolidation. |
| Query planning | Manager binding and the previously failing `.auto()` integration tests pass | Passing these tests is not a benchmark or proof of optimal query plans for every schema. |
| TypedField | Inner-field initialization, copied basic options, and tested delegation pass | Migration reconstruction and complete conversion/validator delegation need further checks. |
| Public exports | Cache and typed ORM exports resolve correctly; package imports remain lazy | Importing model objects themselves still requires an appropriately initialized Django environment. |
| Cache and tasks | Cache/task tracing tests exist; the reported full suite passes | Backend durability, shutdown semantics, failure recovery, and performance require their own evidence. |

## Remaining work

### Tracing

- [ ] Make tracing decorators keep spans open across awaited coroutine execution.
- [ ] Test exception propagation from async functions inside spans.
- [ ] Define and test behavior when telemetry operations themselves fail.
- [ ] Review existing tracing tests before changing exception behavior.

### Serialization and validation

- [ ] Define nested M2M and reverse-relation serialization contracts.
- [ ] Test query counts and unloaded relations for nested serialization.
- [ ] Test recursive-schema termination and nesting limits.
- [ ] Compare `validation.lifecycle` and `validation.unified`, including their exception contracts.
- [ ] Verify complete TypedField migration, conversion, and validator behavior.
- [ ] Check Decimal edge cases against Django validation before claiming semantic equivalence.

### Documentation and reporting

- [ ] Apply and validate the revised status generator against freshly generated coverage XML.
- [ ] Remove coverage-derived readiness labels and unsupported guarantees from documentation.
- [ ] Document `await engine.start()`, `await engine.stop()`, and decorator enqueue semantics.
- [ ] Document that `@nova_task()` uses the shared engine returned by `get_engine()`.
- [ ] Add sync and async `nova_span()` examples and document current decorator limitations.
- [ ] Verify documentation links and build the documentation site.

### Benchmarks

- [ ] Initialize Django before importing Nova model classes in the pyperf script.
- [ ] Separate model construction, schema validation, and serialization measurements.
- [ ] Avoid interpreting different operations as equivalent overhead baselines.
- [ ] Guard standalone benchmark execution and restore the previous GC state on exit.
- [ ] Publish benchmark environment, commands, and raw results alongside conclusions.

### Ecosystem integrations

- [ ] Review DRF, FastAPI, and GraphQL contracts and current coverage reports individually.
- [ ] Record concrete supported scenarios and limitations for each integration.
- [ ] Assess release readiness using behavior, compatibility, and operational evidence.

No integration is labeled untested solely because an earlier table listed 0%
coverage. Update this roadmap when the relevant implementation and verification
have been reviewed.
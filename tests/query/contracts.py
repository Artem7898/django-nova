"""Architectural contracts for P0-3.5 / P0-4.

Contracts ONLY. This module must never import itself or test helpers.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from nova.core.exceptions import NovaConfigurationError, NovaValidationError

ValidationErrors = Mapping[str, Any]
ValidatePayload = Callable[[Mapping[str, Any]], "ValidationReport"]
FieldFactory = Callable[[], Iterable[str]]


@dataclass(frozen=True, slots=True)
class ValidationReport:
    is_valid: bool
    errors: ValidationErrors
    raw: Any = None


@dataclass(frozen=True, slots=True)
class InvalidCase:
    payload: Mapping[str, Any]
    reason: str
    expected_error_fields: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class SchemaProjectionExpectation:
    target: str
    schema_fields: frozenset[str]
    valid_payload: Mapping[str, Any]
    invalid_cases: Sequence[InvalidCase]
    pk_fields: frozenset[str] = frozenset({"id"})
    hidden_fields: frozenset[str] = frozenset()
    allowed_extra_fields: frozenset[str] = frozenset()
    ignore_fields: frozenset[str] = frozenset()
    drf_field_overrides: Mapping[str, str] = field(default_factory=dict)

    @property
    def expected_public_fields(self) -> frozenset[str]:
        return self.schema_fields | self.pk_fields | self.allowed_extra_fields

    @property
    def expected_drf_fields(self) -> frozenset[str]:
        base = self.expected_public_fields
        return frozenset(self.drf_field_overrides.get(f, f) for f in base)

    def normalize_fields(self, exposed_fields: Iterable[str]) -> frozenset[str]:
        return frozenset(exposed_fields) - self.ignore_fields


class SchemaCompilerContract:
    """Contract: Pydantic Schema -> Generated Projection."""

    def __init__(self, expectation: SchemaProjectionExpectation) -> None:
        self.expectation = expectation

    def check_projection(
        self,
        exposed_fields: Iterable[str],
        *,
        use_drf_mapping: bool = False,
    ) -> None:
        exp = self.expectation
        exposed = exp.normalize_fields(exposed_fields)
        expected = exp.expected_drf_fields if use_drf_mapping else exp.expected_public_fields

        missing = expected - exposed
        unexpected = exposed - expected
        leaked = exposed & exp.hidden_fields

        problems: list[str] = []
        if missing:
            problems.append(f"missing expected fields: {sorted(missing)}")
        if unexpected:
            problems.append(f"unexpected fields: {sorted(unexpected)}")
        if leaked:
            problems.append(f"hidden fields leaked: {sorted(leaked)}")

        assert not problems, f"{exp.target}: projection contract failed:\n" + "\n".join(problems)

    def check_deterministic_projection(
        self,
        factory: FieldFactory,
        *,
        runs: int = 3,
        use_drf_mapping: bool = False,
    ) -> None:
        first = frozenset(factory())
        self.check_projection(first, use_drf_mapping=use_drf_mapping)
        for _ in range(max(0, runs - 1)):
            current = frozenset(factory())
            self.check_projection(current, use_drf_mapping=use_drf_mapping)
            assert current == first, f"{self.expectation.target}: projection is not deterministic"

    def check_accepts_valid_payload(self, validate_payload: ValidatePayload) -> None:
        report = validate_payload(self.expectation.valid_payload)
        assert report.is_valid, (
            f"{self.expectation.target}: valid payload rejected: {report.errors}"
        )

    def check_rejects_invalid_payloads(self, validate_payload: ValidatePayload) -> None:
        exp = self.expectation
        assert exp.invalid_cases, f"{exp.target}: invalid_cases must not be empty"
        for case in exp.invalid_cases:
            report = validate_payload(case.payload)
            assert not report.is_valid, f"{exp.target}: invalid payload accepted: {case.reason}"
            assert report.errors, f"{exp.target}: rejected without errors"
            if case.expected_error_fields:
                actual = frozenset(report.errors.keys())
                missing = case.expected_error_fields - actual
                assert not missing, (
                    f"{exp.target}: expected errors for "
                    f"{sorted(case.expected_error_fields)}, got {sorted(actual)}"
                )


@dataclass(frozen=True, slots=True)
class AsyncORMExpectation:
    target: str
    model: Any
    valid_payload: Mapping[str, Any]
    invalid_cases: Sequence[InvalidCase]
    validation_exceptions: tuple[type[Exception], ...] = (NovaValidationError,)
    setup: Callable[[], Awaitable[None]] | None = None


class AsyncORMContract:
    """Contract: async ORM paths enforce the same schema contract."""

    def __init__(self, expectation: AsyncORMExpectation) -> None:
        self.expectation = expectation

    def check_async_public_api(self) -> None:
        manager = getattr(self.expectation.model, "objects", None)
        assert manager is not None, f"{self.expectation.target}: no objects manager"
        queryset = manager.all() if hasattr(manager, "all") else None
        assert hasattr(queryset, "aauto") or hasattr(manager, "aauto"), (
            f"{self.expectation.target}: .aauto() not found"
        )

    def check_aauto_applies_plan(self) -> None:
        from nova.query.planner import build_query_plan

        model_cls = self.expectation.model
        plan = build_query_plan(model_cls)
        qs = model_cls.objects.all().aauto()
        if plan.select_related:
            assert qs.query.select_related, "aauto() did not apply select_related"

    async def check_asave_accepts_valid(self) -> None:
        exp = self.expectation
        if exp.setup is not None:
            await exp.setup()
        instance = exp.model(**exp.valid_payload)
        await instance.asave()

    async def check_asave_rejects_invalid(self) -> None:
        exp = self.expectation
        if exp.setup is not None:
            await exp.setup()
        for case in exp.invalid_cases:
            with pytest.raises(exp.validation_exceptions):
                instance = exp.model(**case.payload)
                await instance.asave()

    async def check_alist_returns_list(self) -> None:
        qs = self.expectation.model.objects.all()
        result = await qs.alist()
        assert isinstance(result, list), "alist() did not return list"


@dataclass(frozen=True, slots=True)
class PlannerExpectation:
    target: str
    model: Any
    expected_select: frozenset[str] = frozenset()
    expected_prefetch: frozenset[str] = frozenset()
    expected_deferred: frozenset[str] = frozenset()
    forbidden_defer: frozenset[str] = frozenset({"id"})
    exact: bool = True


class QueryPlannerContract:
    """Contract: Pydantic Schema -> QueryPlan -> QuerySet."""

    def __init__(self, expectation: PlannerExpectation) -> None:
        self.expectation = expectation

    def _build(self) -> Any:
        from nova.query.planner import build_query_plan

        return build_query_plan(self.expectation.model)

    def check_plan(self) -> Any:
        exp = self.expectation
        plan = self._build()

        if exp.exact:
            assert frozenset(plan.select_related) == exp.expected_select, (
                f"{exp.target}: select_related {plan.select_related}"
            )
            assert frozenset(plan.prefetch_related) == exp.expected_prefetch, (
                f"{exp.target}: prefetch_related {plan.prefetch_related}"
            )
        else:
            assert exp.expected_select <= frozenset(plan.select_related)
            assert exp.expected_prefetch <= frozenset(plan.prefetch_related)

        deferred = frozenset(plan.defer)
        missing = exp.expected_deferred - deferred
        assert not missing, f"{exp.target}: not deferred: {sorted(missing)}"
        leaked = deferred & exp.forbidden_defer
        assert not leaked, f"{exp.target}: forbidden defer: {sorted(leaked)}"
        return plan

    def check_deterministic(self, *, runs: int = 3) -> None:
        first = self._build()
        for _ in range(max(0, runs - 1)):
            assert self._build() == first, f"{self.expectation.target}: plan is not deterministic"

    def check_explain_stable(self, plan: Any) -> None:
        expl = plan.explain()
        assert expl == plan.explain(), "explain() must be stable"
        assert set(expl) == {"select_related", "prefetch_related", "only", "defer"}
        assert all(isinstance(v, tuple) for v in expl.values())

    def check_execution(self, plan: Any) -> None:
        from nova.query.planner import apply_plan

        qs = apply_plan(self.expectation.model.objects.all(), plan)
        if plan.select_related:
            assert qs.query.select_related, "select_related not applied to ORM"
        if plan.prefetch_related:
            assert qs._prefetch_related_lookups, "prefetch_related not applied"

        loaded = set(qs.query.deferred_loading[0])
        if plan.defer:
            expected_deferred = self.expectation.expected_deferred
            assert expected_deferred, "expectation must declare deferred fields"
            assert expected_deferred <= loaded, (
                f"defer not applied: expected {sorted(expected_deferred)}"
            )
        if plan.only:
            assert frozenset(plan.only) <= loaded, f"only not applied: {loaded}"


class CycleSafetyContract:
    """Contract: graph traversal stops on cycles and stays bounded."""

    def check_builds_without_recursion(self, build: Callable[[], Any]) -> Any:
        return build()

    def check_bounded(self, plan: Any, *, max_paths: int = 32) -> None:
        total = (
            len(plan.select_related) + len(plan.prefetch_related) + len(plan.defer) + len(plan.only)
        )
        assert total <= max_paths, f"plan explosion: {total} > {max_paths}"


class UnknownRelationContract:
    """Contract: schema relation missing on model -> explicit Nova error."""

    def check_raises_configuration_error(self, build: Callable[[], Any]) -> None:
        with pytest.raises(NovaConfigurationError):
            build()

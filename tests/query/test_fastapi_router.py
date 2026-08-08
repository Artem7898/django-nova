"""Query contract tests for FastAPI schema compiler."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from nova.ecosystem import to_fastapi_router  # noqa: E402
from tests.models import GrantWithSecret, Lab  # noqa: E402
from tests.query.contracts import SchemaCompilerContract  # noqa: E402
from tests.query.fixtures import (  # noqa: E402
    grant_secret_expectation,
    lab_schema_expectation,
)

# ---------------------------------------------------------------------------
# Lab Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def lab_client() -> TestClient:
    router = to_fastapi_router(Lab)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def lab_contract() -> SchemaCompilerContract:
    # OpenAPI - проекция бизнес-схемы: PK нет (Философия №1)
    return SchemaCompilerContract(lab_schema_expectation("FastAPI: Lab", include_pk=False))


# ---------------------------------------------------------------------------
# Secret Field Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def secret_client() -> TestClient:
    router = to_fastapi_router(GrantWithSecret)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def secret_contract() -> SchemaCompilerContract:
    return SchemaCompilerContract(
        grant_secret_expectation("FastAPI: GrantWithSecret", include_pk=False)
    )


# ---------------------------------------------------------------------------
# Lab Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_fastapi_lab_list_endpoint(lab_client: TestClient) -> None: ...


@pytest.mark.django_db
def test_fastapi_lab_create_valid(lab_client: TestClient) -> None: ...


@pytest.mark.django_db
def test_fastapi_lab_create_invalid_returns_422(lab_client: TestClient) -> None:
    response = lab_client.post("/", json={"name": "Quantum Lab", "budget": -1.0})
    assert response.status_code == 422


def test_fastapi_lab_openapi_projection(
    lab_client: TestClient,
    lab_contract: SchemaCompilerContract,
) -> None:
    response = lab_client.get("/openapi.json")
    assert response.status_code == 200
    spec = response.json()
    assert "LabSchema" in spec["components"]["schemas"]
    component = spec["components"]["schemas"]["LabSchema"]
    lab_contract.check_projection(component["properties"].keys())


# ---------------------------------------------------------------------------
# Secret Field Tests
# ---------------------------------------------------------------------------


def test_fastapi_secret_field_hidden_from_openapi(
    secret_client: TestClient,
    secret_contract: SchemaCompilerContract,
) -> None:
    response = secret_client.get("/openapi.json")
    spec = response.json()
    assert "GrantSchema" in spec["components"]["schemas"]
    component = spec["components"]["schemas"]["GrantSchema"]
    secret_contract.check_projection(component["properties"].keys())
    assert "secret_note" not in component["properties"]


@pytest.mark.django_db
def test_fastapi_secret_create_rejects_short_title(secret_client: TestClient) -> None:
    response = secret_client.post("/", json={"title": "X", "budget": 100.0})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Edge Cases & Flags
# ---------------------------------------------------------------------------


def test_fastapi_model_without_schema_raises() -> None:
    from tests.models import Article

    with pytest.raises(ValueError, match="pydantic_schema"):
        to_fastapi_router(Article)


def test_fastapi_available_flag() -> None:
    from nova.ecosystem.fastapi import FASTAPI_AVAILABLE

    assert FASTAPI_AVAILABLE is True

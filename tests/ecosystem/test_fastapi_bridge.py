"""Tests for FastAPI Auto-Router integration."""
from __future__ import annotations

import pytest

# Safe import check for the whole test file
pytest.importorskip("fastapi", reason="FastAPI not installed")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from nova.ecosystem.fastapi import to_fastapi_router
from tests.models import Lab  # Используем нашу модель с Pydantic-валидацией (budget >= 0)


class TestFastAPIBridge:
    """Test suite for automatic FastAPI router generation."""

    def test_generates_router_with_correct_prefix(self) -> None:
        """to_fastapi_router must return an APIRouter with the specified prefix."""
        router = to_fastapi_router(Lab, prefix="/api/v1/labs")
        assert router.prefix == "/api/v1/labs"

    @pytest.mark.django_db(transaction=True)
    def test_post_rejects_invalid_pydantic_data(self) -> None:
        """
        POST request must reject data that fails Pydantic validation (budget < 0),
        even though standard Django FloatField would accept it.
        """
        app = FastAPI()
        app.include_router(to_fastapi_router(Lab, prefix="/api/labs"))
        client = TestClient(app)

        response = client.post(
            "/api/labs/",
            json={"name": "Bad Lab", "budget": -50.0}
        )

        assert response.status_code == 422  # Unprocessable Entity
        assert "budget" in response.json()["detail"]

    @pytest.mark.django_db(transaction=True)
    def test_post_rejects_invalid_pydantic_data(self) -> None:
        """
        POST request must reject data that fails Pydantic validation (budget < 0).
        Because we inject the real Pydantic schema into FastAPI, FastAPI intercepts
        the error natively and returns a standard 422 Validation Error list.
        """
        app = FastAPI()
        app.include_router(to_fastapi_router(Lab, prefix="/api/labs"))
        client = TestClient(app)

        response = client.post(
            "/api/labs/",
            json={"name": "Bad Lab", "budget": -50.0}
        )

        assert response.status_code == 422  # Unprocessable Entity

        # FastAPI returns a list of error details natively
        errors = response.json()["detail"]
        assert isinstance(errors, list)

        # Find the error related to 'budget'
        budget_error = next(err for err in errors if "budget" in err.get("loc", []))

        # Verify it contains our custom Pydantic message
        assert "Budget cannot be negative" in budget_error["msg"]
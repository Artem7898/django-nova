"""
FastAPI Auto-Router integration.
Dynamically generates CRUD endpoints for NovaModels.
"""

from __future__ import annotations

import inspect
from inspect import Parameter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic import BaseModel

    from nova.typing.models import NovaModel

# Safe FastAPI import
try:
    from fastapi import APIRouter, HTTPException

    FASTAPI_AVAILABLE = True

except ImportError:
    APIRouter = None
    HTTPException = None
    FASTAPI_AVAILABLE = False

from nova.core.exceptions import NovaValidationError


def to_fastapi_router(
    model_cls: type[NovaModel],
    prefix: str = "",
) -> APIRouter:
    """
    Generate CRUD FastAPI router for NovaModel.

    Validation is delegated to the Nova/Pydantic pipeline.
    """

    if not FASTAPI_AVAILABLE:
        raise ImportError("fastapi must be installed to use to_fastapi_router")

    # ------------------------------------------------------------------
    # Validate Nova configuration
    # ------------------------------------------------------------------

    nova_config = getattr(model_cls, "_nova_config", None)

    if nova_config is None:
        raise ValueError(f"{model_cls.__name__} does not define _nova_config")

    pydantic_schema = getattr(
        nova_config,
        "pydantic_schema",
        None,
    )

    if pydantic_schema is None:
        raise ValueError(f"{model_cls.__name__} does not define a pydantic_schema")

    router = APIRouter(prefix=prefix)

    # ------------------------------------------------------------------
    # GET /
    # ------------------------------------------------------------------

    def list_items() -> list[dict[str, Any]]:
        """
        Return all model instances.
        """

        queryset = model_cls.objects.all()

        return [obj.to_dict() for obj in queryset]

    # ------------------------------------------------------------------
    # POST /
    # ------------------------------------------------------------------

    def create_item(data: BaseModel) -> dict[str, Any]:
        """
        Create a new model instance.

        FastAPI automatically validates request body
        using the injected Pydantic schema.
        """

        try:
            payload = data.model_dump()

            instance = model_cls(**payload)

            # IMPORTANT:
            # Validation pipeline must run here.
            instance.save()

            return instance.to_dict()

        except NovaValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=str(exc),
            ) from exc

        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

    # ------------------------------------------------------------------
    # Runtime signature injection
    # ------------------------------------------------------------------

    create_item.__signature__ = inspect.Signature(
        parameters=[
            Parameter(
                name="data",
                kind=Parameter.POSITIONAL_OR_KEYWORD,
                annotation=pydantic_schema,
            )
        ],
        return_annotation=dict[str, Any],
    )

    router.add_api_route(
        path="/",
        endpoint=list_items,
        methods=["GET"],
    )

    router.add_api_route(
        path="/",
        endpoint=create_item,
        methods=["POST"],
        status_code=201,
    )

    return router

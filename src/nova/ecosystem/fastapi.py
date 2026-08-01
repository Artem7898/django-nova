"""FastAPI Auto-Router integration."""
from __future__ import annotations

import inspect
from inspect import Parameter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nova.typing.models import NovaModel

from nova.core.exceptions import NovaValidationError

# Canonical Safe Import (import module, not attributes, to prevent Unknown on missing package)
try:
    import fastapi as _fastapi_module
    _fastapi_available = True
except ImportError:
    _fastapi_module = None  # type: ignore[assignment, misc]
    _fastapi_available = False

# Any alias on the module itself
fastapi: Any = _fastapi_module
FASTAPI_AVAILABLE: bool = _fastapi_available


def to_fastapi_router(
    model_cls: type[NovaModel],
    prefix: str = "",
) -> Any:
    """Generate CRUD FastAPI router for NovaModel."""
    if not FASTAPI_AVAILABLE:
        raise ImportError("fastapi must be installed to use to_fastapi_router")

    nova_config = getattr(model_cls, "_nova_config", None)
    if nova_config is None:
        raise ValueError(f"{model_cls.__name__} does not define _nova_config")

    pydantic_schema = getattr(nova_config, "pydantic_schema", None)
    if pydantic_schema is None:
        raise ValueError(f"{model_cls.__name__} does not define a pydantic_schema")

    router = fastapi.APIRouter(prefix=prefix)

    def list_items() -> list[dict[str, Any]]:
        queryset = model_cls.objects.all()
        return [obj.to_dict() for obj in queryset]

    def create_item(data: Any) -> dict[str, Any]:
        try:
            payload = data.model_dump()
            instance = model_cls(**payload)
            instance.save()
            return instance.to_dict()
        except NovaValidationError as exc:
            raise fastapi.HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise fastapi.HTTPException(status_code=400, detail=str(exc)) from exc

    create_item.__signature__ = inspect.Signature(  # type: ignore[function-member-access]
        parameters=[
            Parameter(
                name="data",
                kind=Parameter.POSITIONAL_OR_KEYWORD,
                annotation=pydantic_schema,
            )
        ],
        return_annotation=dict[str, Any],
    )

    router.add_api_route(path="/", endpoint=list_items, methods=["GET"])
    router.add_api_route(path="/", endpoint=create_item, methods=["POST"], status_code=201)

    return router
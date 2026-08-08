"""
FastAPI integration for Django Nova.

Architecture
------------

                    Pydantic Schema
                           │
                           ▼
                    FastAPI Adapter
                           │
                           ├── request validation
                           ├── response projection
                           └── HTTP routing
                           │
                           ▼
                       NovaModel
                           │
                           ▼
                  Unified ORM Validation

Pydantic remains the only application/data contract.

FastAPI is a transport projection.

The adapter must never convert arbitrary application or infrastructure
exceptions into HTTP validation errors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from pydantic import BaseModel

if TYPE_CHECKING:
    from nova.typing.models import NovaModel


# ---------------------------------------------------------------------------
# Optional dependency boundary
# ---------------------------------------------------------------------------
#
# FastAPI is an optional integration dependency.
#
# Importing Nova must not require FastAPI.
#
# The dynamic import keeps the optional dependency isolated from the core
# package and prevents third-party Unknown types from leaking into Nova's
# internal typing model.
# ---------------------------------------------------------------------------

try:
    import fastapi as _fastapi_module
except ImportError:
    _fastapi_module: Any = None


FASTAPI_AVAILABLE: bool = _fastapi_module is not None


# ---------------------------------------------------------------------------
# Router contract
# ---------------------------------------------------------------------------


@runtime_checkable
class RouterProtocol(Protocol):
    """
    Minimal router contract required by Nova.

    Nova intentionally depends only on the operations it actually uses.
    """

    prefix: str

    def add_api_route(
        self,
        path: str,
        endpoint: Any,
        *,
        methods: list[str],
        status_code: int | None = None,
        response_model: Any | None = None,
    ) -> Any: ...


# ---------------------------------------------------------------------------
# Schema resolution
# ---------------------------------------------------------------------------


def _get_schema(model_cls: type[NovaModel]) -> type[BaseModel]:
    """Resolve the canonical Pydantic schema."""
    config = getattr(model_cls, "_nova_config", None)
    if config is None:
        raise ValueError(f"Model {model_cls.__name__} requires _nova_config.")
    schema = getattr(config, "pydantic_schema", None)
    if schema is None:
        raise ValueError(f"Model {model_cls.__name__} requires pydantic_schema in _nova_config.")

    return schema


# ---------------------------------------------------------------------------
# Runtime annotation helpers
# ---------------------------------------------------------------------------


def _list_response_model(
    schema: type[BaseModel],
) -> Any:
    """
    Build ``list[Schema]`` at runtime.

    The schema is dynamically generated/configured, therefore it cannot
    appear as a static Python type expression.

    Returning Any here deliberately confines dynamic framework metadata to
    the FastAPI integration boundary.
    """
    return list[schema]


def _set_annotation(
    endpoint: Any,
    name: str,
    value: Any,
) -> None:
    """
    Attach runtime FastAPI/Pydantic annotations.

    FastAPI reads endpoint annotations at runtime.

    This helper isolates that dynamic operation from Nova's static typing
    surface.
    """
    annotations = getattr(endpoint, "__annotations__", None)

    if annotations is None:
        annotations = {}

    annotations[name] = value
    endpoint.__annotations__ = annotations


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def to_fastapi_router(
    model_cls: type[NovaModel],
    prefix: str = "",
) -> RouterProtocol:
    if not FASTAPI_AVAILABLE or _fastapi_module is None:
        raise ImportError("fastapi must be installed to use to_fastapi_router().")

    schema = _get_schema(model_cls)
    router_factory: Any = _fastapi_module.APIRouter

    router: Any = router_factory(prefix=prefix)

    # ------------------------------------------------------------------
    # GET /
    # ------------------------------------------------------------------
    def list_items() -> list[dict[str, Any]]:
        queryset = model_cls.objects.all()
        return [obj.to_pydantic().model_dump() for obj in queryset]

    _set_annotation(list_items, "return", _list_response_model(schema))

    router.add_api_route(
        "/",
        list_items,
        methods=["GET"],
        response_model=_list_response_model(schema),
    )

    # ------------------------------------------------------------------
    # POST /
    # ------------------------------------------------------------------
    def create_item(data: Any) -> dict[str, Any]:
        payload = data.model_dump(exclude_unset=False)
        instance = model_cls(**payload)
        instance.save()
        return instance.to_pydantic().model_dump()

    _set_annotation(create_item, "data", schema)
    _set_annotation(create_item, "return", schema)

    router.add_api_route(
        "/",
        create_item,
        methods=["POST"],
        status_code=201,
        response_model=schema,
    )

    return cast(RouterProtocol, router)


__all__ = [
    "FASTAPI_AVAILABLE",
    "RouterProtocol",
    "to_fastapi_router",
]

"""
Component-based Admin UI definitions.
Generates JSON schemas for modern frontends (React/Vue/HTMX).
Replaces Django's monolithic template rendering.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import BaseModel, Field


class UIComponent(BaseModel):
    """Base class for all schema-driven UI nodes."""

    type: str
    props: dict[str, Any] = Field(default_factory=dict)
    events: dict[str, str] = Field(default_factory=dict)


class FormField(UIComponent):
    """Typed form input schema mapping directly to frontend primitives."""

    type: Literal["text", "number", "select", "date", "json"] = "text"  # type: ignore[reportIncompatibleVariableOverride]
    name: str
    label: str
    required: bool = True
    disabled: bool = False


class DataTable(UIComponent):
    """Data table schema for layout specification blocks."""

    type: Literal["datatable"] = "datatable"  # type: ignore[reportIncompatibleVariableOverride]
    columns: list[dict[str, str]] = Field(default_factory=lambda: cast("list[dict[str, str]]", []))
    source_url: str = ""
    searchable: bool = True
    paginated: bool = True


class AdminPage(BaseModel):
    """Root configuration block for a unified administration dashboard screen."""

    title: str
    layout: list[UIComponent]

"""
Component-based Admin UI definitions.
Generates JSON schemas for modern frontends (React/Vue/HTMX).
Replaces Django's monolithic template rendering.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class UIComponent(BaseModel):
    type: str
    props: dict[str, Any] = Field(default_factory=dict)
    events: dict[str, str] = Field(default_factory=dict)


class FormField(UIComponent):
    type: Literal["text", "number", "select", "date", "json"] = "text"
    name: str
    label: str
    required: bool = True
    disabled: bool = False


class DataTable(UIComponent):
    type: Literal["datatable"] = "datatable"
    columns: list[dict[str, str]] = Field(default_factory=list)
    source_url: str = ""
    searchable: bool = True
    paginated: bool = True


class AdminPage(BaseModel):
    title: str
    layout: list[UIComponent]
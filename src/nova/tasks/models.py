"""Data models for task execution."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TaskResult(BaseModel):
    """Unified result model across all backends."""
    id: str = Field(default_factory=lambda: "unknown")
    status: str = "PENDING"  # PENDING, RUNNING, SUCCESS, FAILED
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: Any = None
    error: str | None = None
    attempts: int = 0 
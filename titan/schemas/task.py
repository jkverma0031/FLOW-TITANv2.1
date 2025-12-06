# Path: FLOW/titan/schemas/task.py
from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, validator
from uuid import uuid4
import re

# SECURITY: strict safe name regex
# allow letters, numbers, underscore, 1-64 chars
_VALID_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def new_task_id(prefix: str = "t") -> str:
    return f"{prefix}{uuid4().hex[:8]}"


class TaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class Task(BaseModel):
    """
    Task model represents an abstract planner-level task.
    Parser/adapter will convert Task -> Action[].
    """
    id: str = Field(default_factory=new_task_id)
    name: str
    args: Dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None
    timeout_seconds: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    status: str = Field(default=TaskStatus.PENDING)
    created_at_iso: Optional[str] = None

    class Config:
        extra = "forbid"

    @validator("name")
    def name_must_be_sane(cls, v):
        if not v or not v.strip():
            raise ValueError("Task.name must be non-empty")
        if not _VALID_NAME_RE.match(v):
            raise ValueError("Invalid Task.name: only letters, numbers and underscore allowed (1-64 chars)")
        return v

    def with_updated_status(self, status: str) -> "Task":
        self.status = status
        return self

    def to_summary(self) -> Dict[str, Any]:
        """Small summary that is safe to embed in memory."""
        return {
            "id": self.id,
            "name": self.name,
            "args": self.args,
            "status": self.status,
            "metadata": self.metadata,
        }

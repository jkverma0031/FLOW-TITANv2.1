# titan/schemas/task.py
from __future__ import annotations
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from enum import Enum
from uuid import uuid4

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"

def new_task_id(prefix: str = "task") -> str:
    return f"{prefix}_{uuid4().hex[:8]}"

class Task(BaseModel):
    id: str = Field(default_factory=new_task_id)
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    owner_node_id: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    context_metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    def to_execution_request(self) -> Dict[str, Any]:
        return {
            "task_id": self.id,
            "task_name": self.name,
            "args": self.arguments,
            "context": self.context_metadata
        }

class TaskResult(BaseModel):
    task_id: str
    status: TaskStatus
    success: bool = Field(default=False)
    output: Dict[str, Any] = Field(default_factory=dict)
    logs: Optional[str] = None
    exit_code: Optional[int] = None
    duration_seconds: float = 0.0

    model_config = {"extra": "forbid"}

    @property
    def is_successful(self) -> bool:
        return self.success and self.status == TaskStatus.SUCCESS

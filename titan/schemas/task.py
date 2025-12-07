# Path: titan/schemas/task.py
from __future__ import annotations
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from enum import Enum
from uuid import uuid4

# NOTE: This file must be clean of any old imports like 'NodeType' or 'NodeBase'.

class TaskStatus(str, Enum):
    """Execution status of a single Task definition."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"

def new_task_id(prefix: str = "task") -> str:
    return f"{prefix}_{uuid4().hex[:8]}"

class Task(BaseModel):
    """
    Definition for a single, actionable operation (Task) referenced by a TaskNode in the CFG.
    """
    id: str = Field(default_factory=new_task_id)
    name: str = Field(description="The functional name of the task (e.g., 'list_files', 'compress').")
    
    # Task input arguments derived from the DSL compiler
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Input arguments for the task executor.")
    
    # Metadata for execution context
    owner_node_id: Optional[str] = Field(None, description="The ID of the CFG node executing this task.")
    status: TaskStatus = TaskStatus.PENDING
    
    # Optional dynamic context from runtime (e.g., trust level, file path constraints)
    context_metadata: Dict[str, Any] = Field(default_factory=dict)
    
    def to_execution_request(self) -> Dict[str, Any]:
        """Converts the Task definition into a request format consumable by the WorkerPool/Negotiator."""
        return {
            "task_id": self.id,
            "task_name": self.name,
            "args": self.arguments,
            "context": self.context_metadata
        }

class TaskResult(BaseModel):
    """
    The output structure returned by the Task Executor after completion.
    """
    task_id: str
    status: TaskStatus
    success: bool = Field(description="True if the task completed successfully, false otherwise.")
    output: Dict[str, Any] = Field(default_factory=dict, description="Structured output payload of the task.")
    logs: Optional[str] = None
    exit_code: Optional[int] = None
    duration_seconds: float = 0.0
    
    @property
    def is_successful(self) -> bool:
        return self.success and self.status == TaskStatus.SUCCESS
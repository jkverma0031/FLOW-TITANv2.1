# Path: FLOW/titan/schemas/action.py
from __future__ import annotations
from typing import Any, Dict, Optional, List
from enum import Enum
from pydantic import BaseModel, Field, validator
from uuid import uuid4


class ActionType(str, Enum):
    EXEC = "exec"         # raw command executed in sandbox (shell or python wrapper)
    PLUGIN = "plugin"     # a plugin managed by the system
    HOST = "host"         # hostbridge capability (validated manifest)
    SIMULATED = "simulated"  # simulation / dry-run


def new_action_id(prefix: str = "a") -> str:
    return f"{prefix}{uuid4().hex[:8]}"


class Action(BaseModel):
    """
    Concrete atomic action ready for execution.
    Parser/adapter converts Task -> List[Action]
    """
    id: str = Field(default_factory=new_action_id)
    type: ActionType
    command: Optional[str] = None  # For EXEC: the source string to run
    module: Optional[str] = None  # For PLUGIN/HOST: module/capability name
    args: Dict[str, Any] = Field(default_factory=dict)
    expect_outputs: Optional[List[str]] = None
    timeout_seconds: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"

    @validator("type")
    def enforce_required_fields(cls, v, values):
        # For EXEC actions, 'command' must be present
        if v == ActionType.EXEC:
            if not values.get("command"):
                raise ValueError("EXEC actions must have a `command`")
        # For PLUGIN or HOST, 'module' must be present (capability name)
        if v in (ActionType.PLUGIN, ActionType.HOST):
            if not values.get("module"):
                raise ValueError(f"{v.value.upper()} actions must specify `module` (capability/plugin name)")
        return v

    def to_exec_payload(self) -> Dict[str, Any]:
        """Normalized payload consumed by sandbox/hostbridge runner."""
        return {
            "id": self.id,
            "type": self.type.value,
            "command": self.command,
            "module": self.module,
            "args": self.args,
            "timeout": self.timeout_seconds,
            "metadata": self.metadata,
        }

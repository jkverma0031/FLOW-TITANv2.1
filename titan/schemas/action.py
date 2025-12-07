# Path: titan/schemas/action.py
from __future__ import annotations
from typing import Any, Dict, Optional, List
from enum import Enum
from pydantic import BaseModel, Field, model_validator
from uuid import uuid4


class ActionType(str, Enum):
    # Enum values must be lowercase to correctly enforce ActionType in the Negotiator
    EXEC = "exec"
    PLUGIN = "plugin"
    HOST = "host"
    SIMULATED = "simulated"


def new_action_id(prefix: str = "a") -> str:
    return f"{prefix}{uuid4().hex[:8]}"


class Action(BaseModel):
    id: str = Field(default_factory=new_action_id)
    # The type field will automatically normalize to lowercase string on creation
    # e.g., Action(type="EXEC") -> type="exec"
    type: ActionType
    command: Optional[str] = None
    module: Optional[str] = None
    args: Dict[str, Any] = Field(default_factory=dict)
    expect_outputs: Optional[List[str]] = None
    timeout_seconds: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    # Centralized cross-field validation (Pydantic v2)
    @model_validator(mode="after")
    def validate_action(self):
        if self.type == ActionType.EXEC:
            if not self.command:
                raise ValueError("EXEC actions require a command")
            self.module = None

        if self.type in (ActionType.PLUGIN, ActionType.HOST):
            if not self.module:
                raise ValueError(f"{self.type.value.upper()} actions require a module name")
            self.command = None # Module actions don't use a raw command field

        return self

    def to_exec_payload(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "command": self.command,
            "module": self.module,
            "args": self.args,
            "timeout": self.timeout_seconds,
            "metadata": self.metadata,
        }
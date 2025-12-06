# Path: FLOW/titan/schemas/events.py
from __future__ import annotations
from typing import Any, Dict, Optional, List
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum
import hashlib
import json


class EventType(str, Enum):
    PLAN_CREATED = "PlanCreated"
    DSL_PRODUCED = "DSLProduced"
    AST_PARSED = "ASTParsed"
    NODE_STARTED = "NodeStarted"
    NODE_FINISHED = "NodeFinished"
    LOOP_ITERATION = "LoopIteration"
    RETRY_ATTEMPT = "RetryAttempt"
    DECISION_TAKEN = "DecisionTaken"
    TASK_STARTED = "TaskStarted"
    TASK_FINISHED = "TaskFinished"
    PLAN_COMPLETED = "PlanCompleted"
    ERROR_OCCURRED = "ErrorOccurred"


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class Event(BaseModel):
    id: Optional[str] = None
    type: EventType
    timestamp: str = Field(default_factory=now_iso)
    session_id: Optional[str] = None
    plan_id: Optional[str] = None
    node_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "plan_id": self.plan_id,
            "node_id": self.node_id,
            "payload": self.payload,
            "metadata": self.metadata,
        }

    def to_provenance_entry(self, previous_hash: Optional[str] = None) -> Dict[str, Any]:
        """
        Produce a provenance-ready entry. This returns a stable entry dict
        including a deterministic hash over canonicalized event content.
        """
        event_obj = self.as_dict()
        # canonicalize using json.dumps with sort_keys
        canonical = json.dumps(event_obj, sort_keys=True, separators=(",", ":"))
        entry_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        entry = {
            "event": event_obj,
            "previous_hash": previous_hash,
            "entry_canonical": canonical,
            "entry_hash": entry_hash,
        }
        return entry


# Convenience specialized events
class NodeEvent(Event):
    node_id: str


class TaskEvent(Event):
    task_id: str
    result: Optional[Dict[str, Any]] = None
    success: Optional[bool] = None


class ErrorEvent(Event):
    error_message: str
    exception_name: Optional[str] = None
    traceback: Optional[str] = None

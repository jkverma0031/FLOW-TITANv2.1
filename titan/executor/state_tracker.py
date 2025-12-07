# Path: titan/executor/state_tracker.py
from __future__ import annotations
from typing import Dict, Any, Optional
from enum import Enum
from threading import RLock
import time
import logging

logger = logging.getLogger(__name__)

class NodeState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"

class StateTracker:
    def __init__(self):
        self._lock = RLock()
        self._states: Dict[str, Dict[str, Any]] = {}

    def set_state(self, node_id: str, status: NodeState, error: Optional[str] = None):
        with self._lock:
            if node_id not in self._states:
                self._states[node_id] = {}
            
            self._states[node_id]["status"] = status
            if error:
                self._states[node_id]["error"] = error
            
            if status == NodeState.RUNNING:
                self._states[node_id]["start_time"] = time.time()
            elif status in [NodeState.COMPLETED, NodeState.FAILED, NodeState.SKIPPED]:
                self._states[node_id]["end_time"] = time.time()

    def set_result(self, node_id: str, result: Any):
        with self._lock:
            if node_id not in self._states:
                self._states[node_id] = {}
            self._states[node_id]["result"] = result
            self.set_state(node_id, NodeState.COMPLETED)

    def get_state(self, node_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._states.get(node_id)

    def get_result(self, node_id: str) -> Any:
        with self._lock:
            state = self._states.get(node_id)
            return state.get("result") if state else None

    def get_status(self, node_id: str) -> Optional[NodeState]:
        with self._lock:
            state = self._states.get(node_id)
            return state.get("status") if state else None
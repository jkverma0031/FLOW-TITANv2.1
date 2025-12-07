# Path: FLOW/titan/executor/state_tracker.py
from __future__ import annotations
from threading import RLock
from typing import Dict, Any, Optional
from enum import Enum
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class NodeState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class StateTracker:
    """
    Central runtime state store for nodes/tasks during execution.
    Stores per-node state, start/finish timestamps, result payloads, and error info.
    Thread-safe via RLock.
    """

    def __init__(self):
        self._lock = RLock()
        self._nodes: Dict[str, Dict[str, Any]] = {}  # node_id -> state dict

    def init_node(self, node_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            if node_id not in self._nodes:
                self._nodes[node_id] = {
                    "state": NodeState.PENDING,
                    "metadata": metadata or {},
                    "started_at": None,
                    "finished_at": None,
                    "result": None,
                    "error": None,
                }

    def set_running(self, node_id: str) -> None:
        with self._lock:
            self._ensure_node(node_id)
            self._nodes[node_id]["state"] = NodeState.RUNNING
            self._nodes[node_id]["started_at"] = datetime.utcnow().isoformat()

    def set_success(self, node_id: str, result: Any = None) -> None:
        with self._lock:
            self._ensure_node(node_id)
            self._nodes[node_id]["state"] = NodeState.SUCCESS
            self._nodes[node_id]["finished_at"] = datetime.utcnow().isoformat()
            self._nodes[node_id]["result"] = result

    def set_failed(self, node_id: str, error: str, exc: Optional[Any] = None) -> None:
        with self._lock:
            self._ensure_node(node_id)
            self._nodes[node_id]["state"] = NodeState.FAILED
            self._nodes[node_id]["finished_at"] = datetime.utcnow().isoformat()
            self._nodes[node_id]["error"] = {"message": error, "exc": repr(exc)}

    def set_skipped(self, node_id: str, reason: Optional[str] = None) -> None:
        with self._lock:
            self._ensure_node(node_id)
            self._nodes[node_id]["state"] = NodeState.SKIPPED
            self._nodes[node_id]["finished_at"] = datetime.utcnow().isoformat()
            self._nodes[node_id]["error"] = {"message": reason}

    def get_state(self, node_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._nodes.get(node_id)

    def list_states(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return dict(self._nodes)

    def _ensure_node(self, node_id: str) -> None:
        if node_id not in self._nodes:
            logger.debug("StateTracker: initializing node %s", node_id)
            self.init_node(node_id)

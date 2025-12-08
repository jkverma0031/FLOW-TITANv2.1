# titan/executor/state_tracker.py
from __future__ import annotations
import time
import threading
from typing import Dict, Any, Optional, List

class StateTracker:
    """
    Simple in-memory state tracker. Keeps per-node execution state.
    Keys are node_id strings.
    Each value is a dict like:
    {
      "id": node_id,
      "name": "task_name",
      "status": "pending|running|completed|failed",
      "result": {...},
      "started_at": 0.0,
      "finished_at": 0.0,
      "attempts": int
    }
    """
    def __init__(self):
        self._lock = threading.RLock()
        self._states: Dict[str, Dict[str, Any]] = {}
        # optional mapping from semantic name -> node id(s)
        self._name_index: Dict[str, List[str]] = {}

    def ensure_node(self, node_id: str, name: Optional[str] = None):
        with self._lock:
            if node_id not in self._states:
                self._states[node_id] = {
                    "id": node_id,
                    "name": name or node_id,
                    "status": "pending",
                    "result": None,
                    "started_at": None,
                    "finished_at": None,
                    "attempts": 0
                }
            if name:
                self._name_index.setdefault(name, []).append(node_id)
            return self._states[node_id]

    def set_running(self, node_id: str):
        with self._lock:
            s = self.ensure_node(node_id)
            s["status"] = "running"
            s["started_at"] = time.time()
            s["attempts"] = s.get("attempts", 0) + 1
            return s

    def set_completed(self, node_id: str, result: Any):
        with self._lock:
            s = self.ensure_node(node_id)
            s["status"] = "completed"
            s["result"] = result
            s["finished_at"] = time.time()
            return s

    def set_failed(self, node_id: str, error: str):
        with self._lock:
            s = self.ensure_node(node_id)
            s["status"] = "failed"
            s["result"] = {"error": error}
            s["finished_at"] = time.time()
            return s

    def get(self, node_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._states.get(node_id)

    def list_all(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return dict(self._states)

    def get_state_by_task_name(self, name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            ids = self._name_index.get(name, [])
            if not ids:
                return None
            # return the most recent (last) node id by finished_at
            best = None
            for nid in ids:
                s = self._states.get(nid)
                if s is None:
                    continue
                if best is None or (s.get("finished_at") or 0) > (best.get("finished_at") or 0):
                    best = s
            return best

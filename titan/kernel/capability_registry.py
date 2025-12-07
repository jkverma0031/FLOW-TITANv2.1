# Path: titan/kernel/capability_registry.py
from __future__ import annotations
from typing import Dict, Any
from threading import RLock


class CapabilityRegistry:
    """
    Registry for TITAN capabilities:
      - hostbridge modules
      - sandbox runners
      - docker adapters
      - plugins
    """

    def __init__(self):
        self._lock = RLock()
        self._caps: Dict[str, Any] = {}

    def register(self, name: str, capability: Any):
        with self._lock:
            self._caps[name] = capability

    def get(self, name: str) -> Any:
        with self._lock:
            if name not in self._caps:
                raise KeyError(f"Capability '{name}' not found")
            return self._caps[name]

    def list(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._caps)

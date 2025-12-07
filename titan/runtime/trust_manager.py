# Path: FLOW/titan/runtime/trust_manager.py
from __future__ import annotations
from typing import Dict, Any
from threading import RLock
import logging

logger = logging.getLogger(__name__)


# Predefined trust levels (ordered)
_TRUST_LEVELS = {
    "none": 0,
    "low": 10,
    "medium": 50,
    "high": 90,
    "admin": 100,
}


class TrustManager:
    """
    Lightweight, auditable trust manager.

    Responsibilities:
      - store per-session or per-identity trust scores / attributes
      - answer `permits(required_level)` queries used by Router & Planner
      - allow dynamic modifications (escalation/demotion) with audit metadata
    """

    def __init__(self, default_level: str = "low"):
        self._lock = RLock()
        self._default = default_level if default_level in _TRUST_LEVELS else "low"
        # maps subject_id -> { "level": "low"/"medium", "score": int, "attrs": {...} }
        self._subjects: Dict[str, Dict[str, Any]] = {}

    def _get_level_value(self, level: str) -> int:
        return _TRUST_LEVELS.get(level, 0)

    def create_subject(self, subject_id: str, initial_level: str = None, attrs: Dict[str, Any] = None) -> None:
        with self._lock:
            level = initial_level or self._default
            self._subjects[subject_id] = {"level": level, "score": self._get_level_value(level), "attrs": dict(attrs or {})}

    def set_level(self, subject_id: str, level: str) -> None:
        with self._lock:
            if subject_id not in self._subjects:
                self.create_subject(subject_id, level)
                return
            self._subjects[subject_id]["level"] = level
            self._subjects[subject_id]["score"] = self._get_level_value(level)

    def get_level(self, subject_id: str) -> str:
        with self._lock:
            sub = self._subjects.get(subject_id)
            return sub["level"] if sub else self._default

    def permits(self, subject_id_or_level: str, required_level: str) -> bool:
        """
        Two usages:
          - permits(subject_id, required_level): check subject's stored level
          - permits(level_name, required_level): if first arg looks like a level name, compare directly
        """
        with self._lock:
            # If subject_id_or_level matches known subject
            if subject_id_or_level in self._subjects:
                subject_level = self._subjects[subject_id_or_level]["level"]
                return self._get_level_value(subject_level) >= self._get_level_value(required_level)

            # treat arg as direct level name
            return self._get_level_value(subject_id_or_level) >= self._get_level_value(required_level)

    def audit_subject(self, subject_id: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._subjects.get(subject_id, {"level": self._default, "score": self._get_level_value(self._default), "attrs": {}}))

    def remove_subject(self, subject_id: str) -> None:
        with self._lock:
            if subject_id in self._subjects:
                del self._subjects[subject_id]

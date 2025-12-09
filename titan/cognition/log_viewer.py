# titan/cognition/log_viewer.py
from __future__ import annotations
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("titan.cognition.log_viewer")

class CognitiveLogViewer:
    """
    Lightweight log viewer for cognitive events.
    - tail(limit) returns last N episodes in reverse chronological order
    - filter_by_type(type, limit) filters episodes
    - The viewer uses episodic_store if present, otherwise falls back to in-memory cache if available.
    """

    def __init__(self, app: Dict[str, Any]):
        self.app = app
        self.episodic_store = app.get("episodic_store")
        # optional in-memory fallback queue
        self._fallback_cache: List[Dict[str, Any]] = []
        # try to seed fallback cache from episodic_store.get_recent
        try:
            if self.episodic_store and getattr(self.episodic_store, "get_recent", None):
                self._fallback_cache = list(self.episodic_store.get_recent(500) or [])
        except Exception:
            logger.debug("log_viewer seed failed")

    def tail(self, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            if self.episodic_store:
                if getattr(self.episodic_store, "get_recent", None):
                    return list(self.episodic_store.get_recent(limit) or [])
                if getattr(self.episodic_store, "query", None):
                    res = self.episodic_store.query({"limit": limit})
                    return list(res or [])
            # fallback to cached list
            return list(self._fallback_cache[-limit:])
        except Exception:
            logger.exception("tail failed")
            return []

    def filter_by_type(self, typ: str, limit: int = 100) -> List[Dict[str, Any]]:
        out = []
        try:
            items = self.tail(limit * 5)
            for it in reversed(items):
                if it.get("type") == typ or (it.get("payload") or {}).get("type") == typ:
                    out.append(it)
                    if len(out) >= limit:
                        break
            return out
        except Exception:
            logger.exception("filter_by_type failed")
            return []

    def append_to_fallback(self, item: Dict[str, Any]) -> None:
        try:
            self._fallback_cache.append(item)
            if len(self._fallback_cache) > 2000:
                self._fallback_cache = self._fallback_cache[-1000:]
        except Exception:
            logger.exception("append_to_fallback failed")

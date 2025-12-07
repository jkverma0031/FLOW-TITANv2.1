# Path: FLOW/titan/runtime/context_store.py
from __future__ import annotations
from typing import Any, Dict, Optional
from threading import RLock
import json
import os
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


class ContextStore:
    """
    Simple per-session context store.

    Features:
      - Thread-safe key/value store
      - Optional persistence to a JSON file (per-session) when constructed with persistence_path
      - Typed getters with default values
      - Merge/patch helpers
      - created_at/updated_at metadata stored internally (not exposed as extra fields)
    """

    def __init__(self, session_id: str, persistence_path: Optional[str] = None, autosave: bool = False):
        self.session_id = session_id
        self._lock = RLock()
        self._data: Dict[str, Any] = {}
        self._meta: Dict[str, str] = {}
        self.persistence_path = persistence_path
        self.autosave = autosave

        if self.persistence_path:
            os.makedirs(os.path.dirname(self.persistence_path) or ".", exist_ok=True)
            self._load_from_disk()

    # ---- persistence ----
    def _load_from_disk(self) -> None:
        try:
            if os.path.exists(self.persistence_path):
                with open(self.persistence_path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                    if isinstance(raw, dict):
                        self._data = raw.get("data", {})
                        self._meta = raw.get("meta", {})
        except Exception as e:
            logger.exception("Failed to load ContextStore from disk: %s", e)

    def _save_to_disk(self) -> None:
        if not self.persistence_path:
            return
        tmp = f"{self.persistence_path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"data": self._data, "meta": self._meta}, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.persistence_path)
        except Exception as e:
            logger.exception("Failed to save ContextStore to disk: %s", e)

    # ---- API ----
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._meta[key] = datetime.now(timezone.utc).isoformat()
            if self.autosave:
                self._save_to_disk()

    def delete(self, key: str) -> None:
        with self._lock:
            if key in self._data:
                del self._data[key]
            if key in self._meta:
                del self._meta[key]
            if self.autosave:
                self._save_to_disk()

    def get_all(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def patch(self, patch_dict: Dict[str, Any]) -> None:
        """Shallow merge keys from patch_dict into context."""
        with self._lock:
            self._data.update(patch_dict)
            now = datetime.now(timezone.utc).isoformat()
            for k in patch_dict:
                self._meta[k] = now
            if self.autosave:
                self._save_to_disk()

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._meta.clear()
            if self.autosave:
                self._save_to_disk()

    def keys(self):
        with self._lock:
            return list(self._data.keys())

    def contains(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def to_serializable(self) -> Dict[str, Any]:
        with self._lock:
            return {"session_id": self.session_id, "data": self._data, "meta": self._meta}

    def close(self) -> None:
        if self.persistence_path:
            try:
                self._save_to_disk()
            except Exception:
                pass

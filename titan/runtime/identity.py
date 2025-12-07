# Path: FLOW/titan/runtime/identity.py
from __future__ import annotations
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict
from threading import RLock
import uuid
import logging

logger = logging.getLogger(__name__)


@dataclass
class Identity:
    id: str
    display_name: str
    kind: str = "user"  # "user", "agent", "service"
    metadata: Dict[str, Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class IdentityManager:
    """
    Manage identities (users, agents, services).
    Identity objects are light and kept in-memory. For persistence adapt to DB.
    """

    def __init__(self):
        self._lock = RLock()
        self._idents: Dict[str, Identity] = {}

    def create(self, display_name: str, kind: str = "user", metadata: Dict[str, Any] = None) -> Identity:
        with self._lock:
            rid = f"id_{uuid.uuid4().hex[:8]}"
            ident = Identity(id=rid, display_name=display_name, kind=kind, metadata=metadata or {})
            self._idents[rid] = ident
            return ident

    def get(self, ident_id: str) -> Optional[Identity]:
        with self._lock:
            return self._idents.get(ident_id)

    def find_by_name(self, display_name: str) -> Optional[Identity]:
        with self._lock:
            for ident in self._idents.values():
                if ident.display_name == display_name:
                    return ident
            return None

    def list_all(self) -> Dict[str, Identity]:
        with self._lock:
            return dict(self._idents)

    def remove(self, ident_id: str) -> None:
        with self._lock:
            if ident_id in self._idents:
                del self._idents[ident_id]

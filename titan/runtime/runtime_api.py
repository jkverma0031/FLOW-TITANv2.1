# Path: FLOW/titan/runtime/runtime_api.py
from __future__ import annotations
from typing import Optional, Dict, Any
import logging

from .session_manager import SessionManager

logger = logging.getLogger(__name__)


class RuntimeAPI:
    """
    Lightweight facade used by planner/executor to access runtime services.
    Designed to keep higher-level code succinct and stable.
    """

    def __init__(self, session_manager: SessionManager):
        self._sm = session_manager

    def create_session(self, owner_display_name: Optional[str] = None, ttl_seconds: Optional[int] = None) -> str:
        # NOTE: ttl_seconds is handled by initial_metadata in SessionManager.create, 
        # but we keep the signature here for compatibility and documentation.
        initial_metadata = {}
        if ttl_seconds is not None:
             initial_metadata["_ttl"] = ttl_seconds
             
        return self._sm.create(owner_display_name=owner_display_name, initial_metadata=initial_metadata)

    def get_context(self, session_id: str):
        return self._sm.get_context(session_id)

    def get_trust(self):
        return self._sm.get_trust_manager()

    def get_identity_mgr(self):
        return self._sm.get_identity_manager()

    def end_session(self, session_id: str):
        return self._sm.end_session(session_id)
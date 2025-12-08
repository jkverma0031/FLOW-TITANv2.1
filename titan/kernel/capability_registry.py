# titan/kernel/capability_registry.py
from __future__ import annotations
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

class CapabilityRegistry:
    """
    Registry of runtime capabilities (sandbox, docker, hostbridge, plugins).
    It allows subsystems to register capabilities with metadata and for the Planner
    to discover available tools/manifests.
    """

    def __init__(self):
        self._caps: Dict[str, Dict[str, Any]] = {}

    def register(self, name: str, obj: Any, *, metadata: Optional[Dict[str, Any]] = None):
        """
        Register a capability object with optional metadata dictionary.
        - name: unique string
        - obj: any Python object representing the capability (runner, plugin, service)
        - metadata: optional information to help Planner and Negotiator (e.g., manifest)
        """
        if not name:
            raise ValueError("Capability name required")
        self._caps[name] = {"object": obj, "metadata": metadata or {}}
        logger.info("CapabilityRegistry: registered %s", name)

    def get(self, name: str) -> Optional[Any]:
        entry = self._caps.get(name)
        return entry.get("object") if entry else None

    def get_metadata(self, name: str) -> Optional[Dict[str, Any]]:
        entry = self._caps.get(name)
        return entry.get("metadata") if entry else None

    def list(self) -> List[str]:
        return list(self._caps.keys())

    def unregister(self, name: str):
        if name in self._caps:
            del self._caps[name]
            logger.info("CapabilityRegistry: unregistered %s", name)

    def export_manifests(self) -> Dict[str, Dict[str, Any]]:
        """
        Export metadata/manifests for all registered capabilities.
        Planner will use these manifests when asking LLM to generate DSL.
        """
        out = {}
        for name, entry in self._caps.items():
            meta = entry.get("metadata") or {}
            # If the object offers get_manifest, include it
            obj = entry.get("object")
            if hasattr(obj, "get_manifest"):
                try:
                    meta_manifest = obj.get_manifest()
                    meta.setdefault("manifest", meta_manifest)
                except Exception:
                    logger.exception("Failed to fetch manifest for capability %s", name)
            out[name] = meta
        return out

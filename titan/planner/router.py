# Path: FLOW/titan/planner/router.py
"""
Router:
Determines which system capability is appropriate for a Task.

Example:
Task(name="list_files") → sandbox / hostbridge depending on trust level
Task(name="upload") → plugin or hostbridge depending on manifests
"""

from __future__ import annotations
from typing import Optional, Dict, Any
from titan.runtime.trust_manager import TrustManager


class Router:
    def __init__(self, capability_manifest: Dict[str, Any]):
        """
        capability_manifest:
            Example:
            {
                "list_files": { "backend": "host", "trust": "low" },
                "compress":   { "backend": "exec", "trust": "low" },
                "upload":     { "backend": "plugin", "trust": "medium" }
            }
        """
        self.manifest = capability_manifest

    def route(self, task_name: str, trust: TrustManager) -> str:
        """
        Return backend choice: "exec", "host", "plugin", "simulated".
        """
        entry = self.manifest.get(task_name)
        if not entry:
            return "exec"  # fallback: generic sandbox exec

        required_trust = entry.get("trust", "low")
        backend = entry.get("backend", "exec")

        if not trust.permits(required_trust):
            return "simulated"

        return backend

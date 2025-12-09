# titan/kernel/diagnostics.py
from __future__ import annotations
from typing import Dict, Any
from threading import RLock
import time
import os
import logging

# optional psutil (if available)
try:
    import psutil
    _HAS_PSUTIL = True
except Exception:
    _HAS_PSUTIL = False

logger = logging.getLogger(__name__)


class KernelDiagnostics:
    """
    Diagnostics helper for the Kernel. Designed to be defensive:
    - tolerate missing app components during early boot
    - use fallbacks if psutil is not installed
    """

    def __init__(self, app_context):
        self.app = app_context
        self._lock = RLock()
        self.boot_time = time.time()

    def _safe_get_registered_services(self) -> list:
        try:
            # AppContext implementations may expose list_services() or dump()
            if hasattr(self.app, "list_services"):
                svc = self.app.list_services()
                # list_services may return dict or list
                if isinstance(svc, dict):
                    return list(svc.keys())
                if isinstance(svc, (list, tuple)):
                    return list(svc)
                return [str(svc)]
            if hasattr(self.app, "dump"):
                data = self.app.dump()
                if isinstance(data, dict):
                    return list(data.keys())
                return []
        except Exception:
            logger.exception("Failed to read registered services from AppContext")
        return []

    def _safe_get_registered_capabilities(self) -> list:
        try:
            cap_registry = self.app.get("cap_registry", None) if hasattr(self.app, "get") else None
            if cap_registry is None:
                return []
            if hasattr(cap_registry, "list"):
                res = cap_registry.list()
                return list(res) if res is not None else []
            # fallback: try attribute
            if hasattr(cap_registry, "registered"):
                return list(getattr(cap_registry, "registered"))
        except Exception:
            logger.exception("Failed to read capability registry")
        return []

    def system_health(self) -> Dict[str, Any]:
        """
        Return a basic system health dictionary.
        """
        with self._lock:
            uptime = time.time() - self.boot_time

            # memory & cpu
            mem = -1
            cpu = -1.0
            try:
                if _HAS_PSUTIL:
                    proc = psutil.Process(os.getpid())
                    mem = proc.memory_info().rss
                    cpu = proc.cpu_percent(interval=0.05)
                else:
                    # best-effort fallback
                    mem = -1
                    cpu = -1.0
            except Exception:
                logger.exception("psutil sampling failed in KernelDiagnostics")

            registered_services = self._safe_get_registered_services()
            registered_capabilities = self._safe_get_registered_capabilities()

            return {
                "uptime_seconds": uptime,
                "memory_bytes": mem,
                "cpu_percent": cpu,
                "registered_services": registered_services,
                "registered_capabilities": registered_capabilities,
            }

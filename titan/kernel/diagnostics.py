# Path: titan/kernel/diagnostics.py
from __future__ import annotations
from typing import Dict, Any
from threading import RLock
import time
import psutil
import os


class KernelDiagnostics:
    """
    Provides:
        - Memory usage
        - CPU usage
        - Active sessions
        - Registered capabilities
        - Health status
    """

    def __init__(self, app_context):
        self.app = app_context
        self._lock = RLock()
        self.boot_time = time.time()

    def system_health(self) -> Dict[str, Any]:
        process = psutil.Process(os.getpid())
        mem = process.memory_info().rss
        cpu = process.cpu_percent(interval=0.05)

        return {
            "uptime_seconds": time.time() - self.boot_time,
            "memory_bytes": mem,
            "cpu_percent": cpu,
            "registered_services": list(self.app.all_services().keys()),
            "registered_capabilities": list(self.app.get("cap_registry").list().keys()),
        }

# Path: titan/kernel/kernel.py
from __future__ import annotations
import logging
from typing import Optional, Callable, Any

from .app_context import AppContext
from .lifecycle import Lifecycle
from .startup import perform_kernel_startup
from .diagnostics import KernelDiagnostics

logger = logging.getLogger(__name__)


class Kernel:
    def __init__(self, cfg: Optional[dict] = None):
        self.app = AppContext()
        # pass config through to startup wiring
        perform_kernel_startup(self.app, cfg=cfg or {})
        self.lifecycle = Lifecycle(self.app)
        self.diagnostics = KernelDiagnostics(self.app)

    def start(self):
        logger.info("[Kernel] Starting TITAN…")
        self.lifecycle.startup()

    def shutdown(self):
        logger.info("[Kernel] Shutting down TITAN…")
        self.lifecycle.shutdown()

    def health(self):
        return self.diagnostics.system_health()

    def run_plan(self, plan: Any, session_id: str, replanner_fn: Optional[Callable[[dict], Any]] = None) -> dict:
        """
        Run a plan via the orchestrator. Accepts optional replanner hook.
        """
        orch = self.app.get("orchestrator")
        # execute_plan signature: execute_plan(plan, session_id, replanner_fn=None)
        return orch.execute_plan(plan, session_id, replanner_fn=replanner_fn)

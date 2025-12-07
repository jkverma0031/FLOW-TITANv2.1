# Path: titan/kernel/lifecycle.py
from __future__ import annotations
import logging
from typing import Optional
from titan.observability.tracing import tracer

logger = logging.getLogger(__name__)

class Lifecycle:
    """
    Orchestrates TITAN startup/shutdown.
    Handles:
      - sandbox cleanup (orphan containers)
      - SessionManager start/stop
      - Orchestrator shutdown
      - tracing metadata
    """

    def __init__(self, app):
        self.app = app

    def startup(self):
        logger.info("[Lifecycle] startup beginning")
        logger.info("[Lifecycle] tracing engine active", extra={"trace_id": tracer._new_trace_id()})

        # 1. Cleanup sandbox
        try:
            cleanup_fn = self.app.get("sandbox_cleanup")
            cleanup_fn()
            logger.info("[Lifecycle] sandbox cleanup executed")
        except Exception:
            logger.exception("Sandbox cleanup failed during startup")

        # 2. Start SessionManager
        try:
            sm = self.app.get("session_manager")
            sm.start()
            logger.info("[Lifecycle] session manager started")
        except Exception:
            logger.exception("Failed to start SessionManager during startup")

        logger.info("[Lifecycle] startup complete")

    def shutdown(self):
        logger.info("[Lifecycle] shutdown beginning")

        # 1. Stop SessionManager
        try:
            sm = self.app.get("session_manager")
            sm.stop()
            logger.info("[Lifecycle] session manager stopped")
        except Exception:
            logger.exception("Failed to stop SessionManager")

        # 2. Shutdown Orchestrator
        try:
            orch = self.app.get("orchestrator")
            if hasattr(orch, "shutdown"):
                orch.shutdown()
                logger.info("[Lifecycle] orchestrator shutdown called")
        except Exception:
            logger.exception("Failed orchestrator shutdown")

        # 3. Cleanup sandbox again
        try:
            cleanup_fn = self.app.get("sandbox_cleanup")
            cleanup_fn()
            logger.info("[Lifecycle] sandbox cleanup executed on shutdown")
        except Exception:
            logger.exception("Sandbox cleanup failed")

        logger.info("[Lifecycle] shutdown complete")

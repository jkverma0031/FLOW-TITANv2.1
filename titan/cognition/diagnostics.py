# titan/cognition/diagnostics.py
from __future__ import annotations
import asyncio
import logging
import time
from typing import Any, Dict

logger = logging.getLogger("titan.cognition.diagnostics")

class CognitiveDiagnostics:
    """
    Provides health checks across cognition stack:
    - checks presence and basic responsiveness of key components
    - gathers metrics: queue sizes, last tick times, failure counts
    - returns a structured report for operator inspection
    """

    def __init__(self, app: Dict[str, Any]):
        self.app = app
        self.autonomy_engine = app.get("autonomy_engine")
        self.skill_manager = app.get("skill_manager")
        self.memory_consolidator = app.get("memory_consolidator")
        self.reflection_engine = app.get("reflection_engine")
        self.temporal_scheduler = app.get("temporal_scheduler")
        self.event_bus = app.get("event_bus")
        self.episodic_store = app.get("episodic_store")
        self.metrics = app.get("metrics_adapter")

    async def run_full_check(self) -> Dict[str, Any]:
        """
        Runs checks and returns a dict with status for each component.
        Each component has {ok: bool, details: str, data: {...}}
        """
        report = {"ts": time.time(), "components": {}}
        report["components"]["autonomy_engine"] = await self._check_autonomy_engine()
        report["components"]["skill_manager"] = await self._check_skill_manager()
        report["components"]["memory_consolidator"] = await self._check_service(self.memory_consolidator, "memory_consolidator")
        report["components"]["reflection_engine"] = await self._check_service(self.reflection_engine, "reflection_engine")
        report["components"]["temporal_scheduler"] = await self._check_service(self.temporal_scheduler, "temporal_scheduler")
        report["components"]["event_bus"] = await self._check_event_bus()
        report["components"]["episodic_store"] = await self._check_episodic_store()
        # metrics snapshot if available
        try:
            if self.metrics:
                report["metrics_snapshot"] = self.metrics.snapshot()
        except Exception:
            report["metrics_snapshot"] = {"error": "metrics_failed"}
        return report

    async def _check_autonomy_engine(self) -> Dict[str, Any]:
        if not self.autonomy_engine:
            return {"ok": False, "details": "not configured"}
        try:
            health = await self.autonomy_engine.health() if hasattr(self.autonomy_engine, "health") else {"running": True}
            return {"ok": True, "details": "running", "data": health}
        except Exception:
            logger.exception("autonomy_engine health check failed")
            return {"ok": False, "details": "health_check_failed"}

    async def _check_skill_manager(self) -> Dict[str, Any]:
        if not self.skill_manager:
            return {"ok": False, "details": "not configured"}
        try:
            skills = self.skill_manager.list_skills() if hasattr(self.skill_manager, "list_skills") else {}
            loaded = list(self.skill_manager._skills.keys()) if hasattr(self.skill_manager, "_skills") else []
            return {"ok": True, "details": "running", "data": {"registered": len(skills), "loaded": len(loaded), "loaded_names": loaded}}
        except Exception:
            logger.exception("skill_manager check failed")
            return {"ok": False, "details": "check_failed"}

    async def _check_service(self, svc, name: str) -> Dict[str, Any]:
        if not svc:
            return {"ok": False, "details": f"{name} not configured"}
        try:
            if hasattr(svc, "health"):
                h = await svc.health()
                return {"ok": True, "details": "service healthy", "data": h}
            # fallback: basic attribute checks
            return {"ok": True, "details": "service present"}
        except Exception:
            logger.exception("%s check failed", name)
            return {"ok": False, "details": "service_check_failed"}

    async def _check_event_bus(self) -> Dict[str, Any]:
        if not self.event_bus:
            return {"ok": False, "details": "not configured"}
        try:
            # try a lightweight publish/subscribe probe if available
            if getattr(self.event_bus, "health_check", None):
                ok = self.event_bus.health_check()
                return {"ok": bool(ok), "details": "event_bus health_check", "data": {"ok": ok}}
            return {"ok": True, "details": "event_bus present"}
        except Exception:
            logger.exception("event_bus check failed")
            return {"ok": False, "details": "eventbus_check_exception"}

    async def _check_episodic_store(self) -> Dict[str, Any]:
        s = self.episodic_store
        if not s:
            return {"ok": False, "details": "not configured"}
        try:
            # try fetching last 1 item
            if getattr(s, "get_recent", None):
                recs = s.get_recent(1)
                return {"ok": True, "details": "episodic_store accessible", "data": {"recent_count": len(recs)}}
            if getattr(s, "query", None):
                q = s.query({"limit": 1})
                return {"ok": True, "details": "episodic_store queryable", "data": {"sample": list(q)[:1]}}
            return {"ok": True, "details": "episodic_store present"}
        except Exception:
            logger.exception("episodic_store check failed")
            return {"ok": False, "details": "episodic_store_error"}

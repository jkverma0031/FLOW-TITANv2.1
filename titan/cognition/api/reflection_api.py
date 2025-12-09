# titan/cognition/api/reflection_api.py
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from fastapi import FastAPI
from typing import Any, Dict, List, Optional
import asyncio
import logging
import time

logger = logging.getLogger("titan.cognition.api.reflection")

def _get_app_container() -> Dict[str, Any]:
    """
    Replace this stub as needed. In your startup you should pass `app` dict
    into FastAPI app as `app.state.kernel_app = {...}` or similar.
    This factory expects the FastAPI app object to store Titan app dict under state.kernel_app.
    """
    # This function will be overridden by the dependency below when included in FastAPI app
    raise RuntimeError("Kernel app container not bound. Use `include_router_with_app` to mount router.")

def include_router_with_app(fastapi_app: FastAPI, kernel_app: Dict[str, Any], prefix: str = "/cognition"):
    """
    Convenience: mount router after binding a dependency that returns the kernel app dict.
    Usage:
        from titan.cognition.api.reflection_api import include_router_with_app
        include_router_with_app(app, kernel_app)
    """
    router = APIRouter(prefix=prefix)
    # bind dependency closure
    def _app_getter() -> Dict[str, Any]:
        return kernel_app

    # endpoints
    @router.get("/reflection/recent", response_model=List[Dict[str, Any]])
    async def get_recent_reflections(limit: int = 20, app: Dict[str, Any] = Depends(_app_getter)):
        engine = app.get("reflection_engine")
        if not engine:
            raise HTTPException(status_code=404, detail="Reflection engine not available")
        items = await engine.get_recent_reflections(limit=limit)
        return items

    @router.post("/reflection/run", response_model=Dict[str, Any])
    async def run_reflection_now(app: Dict[str, Any] = Depends(_app_getter)):
        engine = app.get("reflection_engine")
        if not engine:
            raise HTTPException(status_code=404, detail="Reflection engine not available")
        try:
            res = await asyncio.wait_for(engine.run_once(), timeout=120.0)
            return {"status": "ok", "result": res}
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Reflection run timed out")
        except Exception as e:
            logger.exception("Reflection run failed")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/reflection/lessons", response_model=List[Dict[str, Any]])
    async def get_lessons(limit: int = 50, app: Dict[str, Any] = Depends(_app_getter)):
        # Extract recent reflection.lesson events from episodic_store or event log
        try:
            episodic = app.get("episodic_store")
            if episodic and getattr(episodic, "query", None):
                res = episodic.query({"type": "reflection"}) or []
                out = []
                for r in reversed(list(res))[:limit]:
                    out.append(r)
                return out
            # fallback: reflection_engine recent reflections
            engine = app.get("reflection_engine")
            if engine:
                items = await engine.get_recent_reflections(limit=limit)
                return items
            return []
        except Exception:
            logger.exception("get_lessons failed")
            raise HTTPException(status_code=500, detail="internal error")

    @router.get("/diagnostics", response_model=Dict[str, Any])
    async def diagnostics_report(app: Dict[str, Any] = Depends(_app_getter)):
        diag = app.get("cognition_diagnostics")
        if not diag:
            raise HTTPException(status_code=404, detail="Diagnostics service not available")
        try:
            report = await diag.run_full_check()
            return report
        except Exception:
            logger.exception("diagnostics failed")
            raise HTTPException(status_code=500, detail="diagnostics error")

    @router.post("/tuner/adjust", response_model=Dict[str, Any])
    async def tuner_adjust(action: Dict[str, Any], app: Dict[str, Any] = Depends(_app_getter)):
        tuner = app.get("auto_tuner")
        if not tuner:
            raise HTTPException(status_code=404, detail="AutoTuner not available")
        try:
            res = tuner.handle_action(action)
            return {"status": "ok", "result": res}
        except Exception:
            logger.exception("tuner adjust failed")
            raise HTTPException(status_code=500, detail="tuner adjust error")

    @router.get("/logs", response_model=List[Dict[str, Any]])
    async def get_logs(limit: int = 100, app: Dict[str, Any] = Depends(_app_getter)):
        viewer = app.get("cognition_log_viewer")
        if not viewer:
            raise HTTPException(status_code=404, detail="Log viewer not available")
        try:
            return viewer.tail(limit=limit)
        except Exception:
            logger.exception("get_logs failed")
            raise HTTPException(status_code=500, detail="log viewer error")

    @router.get("/metrics", response_model=Dict[str, Any])
    async def get_metrics(app: Dict[str, Any] = Depends(_app_getter)):
        metrics = app.get("metrics_adapter")
        if not metrics:
            raise HTTPException(status_code=404, detail="Metrics adapter not available")
        try:
            return metrics.snapshot()
        except Exception:
            logger.exception("get_metrics failed")
            raise HTTPException(status_code=500, detail="metrics error")

    fastapi_app.include_router(router)
    logger.info("Reflection API router included under %s", prefix)
    return router

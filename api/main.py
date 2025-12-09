# api/main.py
from __future__ import annotations
import logging
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import traceback
import asyncio
# try to import Kernel if present
try:
    from titan.kernel.kernel import Kernel
    from titan.kernel.diagnostics import KernelDiagnostics
    _HAS_KERNEL = True
except Exception:
    Kernel = None
    KernelDiagnostics = None
    _HAS_KERNEL = False

logger = logging.getLogger("titan.api")
app = FastAPI(title="FLOW-TITAN v2.1 API", version="0.1")

_kernel_singleton = None

def get_kernel():
    global _kernel_singleton
    if _kernel_singleton is None:
        if not _HAS_KERNEL:
            raise RuntimeError("Kernel not available in this environment.")
        _kernel_singleton = Kernel()
        try:
            # prefer asynchronous start if available
            maybe_start = getattr(_kernel_singleton, "start", None)
            if callable(maybe_start):
                res = maybe_start()
                # handle coroutine start()
                import asyncio
                if asyncio.iscoroutine(res):
                    asyncio.get_event_loop().run_until_complete(res)
        except Exception:
            logger.exception("Kernel failed to start during get_kernel()")
    return _kernel_singleton

@app.get("/health")
async def health():
    if not _HAS_KERNEL:
        return JSONResponse({"status": "partial", "message": "kernel not importable"})
    try:
        k = get_kernel()
        diag = KernelDiagnostics(k.app) if KernelDiagnostics else None
        if diag:
            return JSONResponse({"status": "ok", "diagnostics": diag.system_health()})
        return JSONResponse({"status": "ok", "message": "kernel running"})
    except Exception as e:
        logger.exception("health check failed")
        return JSONResponse({"status": "error", "error": str(e), "trace": traceback.format_exc()})

@app.post("/run_plan")
async def run_plan(plan_id: str | None = None, dsl: str | None = None):
    if not _HAS_KERNEL:
        raise HTTPException(status_code=503, detail="Kernel not available")
    k = get_kernel()
    try:
        if dsl:
            planner = k.app.get("planner")
            if planner is None:
                raise RuntimeError("Planner not registered")
            plan = planner.plan_from_dsl(dsl)
            if asyncio.iscoroutine(plan):
                import asyncio as _asyncio
                plan = _asyncio.get_event_loop().run_until_complete(plan)
        else:
            runtime = k.app.get("runtime")
            if runtime is None:
                raise RuntimeError("Runtime not registered")
            plan = runtime.load_plan(plan_id)
            if plan is None:
                raise RuntimeError("plan not found")
        orchestrator = k.app.get("orchestrator")
        if orchestrator is None:
            raise RuntimeError("Orchestrator not registered")
        res = orchestrator.execute_plan(plan)
        if asyncio.iscoroutine(res):
            import asyncio as _asyncio
            res = _asyncio.get_event_loop().run_until_complete(res)
        return JSONResponse({"status": "started", "plan_id": getattr(plan, "id", None), "summary": getattr(plan, "to_summary", lambda: {})()})
    except Exception as e:
        logger.exception("run_plan failed")
        raise HTTPException(status_code=500, detail={"error": str(e), "trace": traceback.format_exc()})

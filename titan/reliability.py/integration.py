# titan/reliability/integration.py
"""
Supervisor integration helpers.

Use attach_supervisor(app, engine=None, skill_manager=None, services=[]) to:
 - create Supervisor if missing
 - watch core engine loops and background services (memory_consolidator, reflection, temporal_scheduler)
 - wrap worker_pool.submit to ensure tasks executed via supervisor for safety
 - provide a small health-probe integration
"""

from __future__ import annotations
import asyncio
import logging
import time
from typing import Any, Dict, Iterable, Optional

from .supervisor import Supervisor

logger = logging.getLogger("titan.reliability.integration")

def attach_supervisor(app: Dict[str, Any], engine: Optional[Any] = None, skill_manager: Optional[Any] = None, *,
                      watch_services: Optional[Iterable[str]] = None):
    """
    Attach supervisor and register common services for monitoring.
    watch_services is a list of names to auto-watch: options include:
      - 'autonomy_engine', 'memory_consolidator', 'reflection_engine', 'temporal_scheduler'
    """
    sup = app.get("supervisor")
    if not sup:
        sup = Supervisor(app)
        app["supervisor"] = sup

    # helper to create coro_factory from a service object and its main loop method
    def _coro_factory_for(obj, method_name: str = "start"):
        async def _factory():
            meth = getattr(obj, method_name, None)
            if not meth:
                # if no start method, attempt to run a noop loop if object is a coroutine or function
                if asyncio.iscoroutinefunction(obj):
                    await obj()
                return
            # call start; if start returns a coroutine, await it; else if it loops internally, allow it to run until cancelled
            res = meth()
            if asyncio.iscoroutine(res):
                await res
        return _factory

    # default watch list mapping to actual objects in app
    mapping = {
        "autonomy_engine": app.get("autonomy_engine"),
        "memory_consolidator": app.get("memory_consolidator"),
        "reflection_engine": app.get("reflection_engine"),
        "temporal_scheduler": app.get("temporal_scheduler"),
    }

    services = watch_services or list(mapping.keys())
    for svc_name in services:
        svc_obj = mapping.get(svc_name)
        if svc_obj:
            try:
                # supervise by wrapping the object's 'start' or 'run' method
                coro_fac = _coro_factory_for(svc_obj, method_name="start")
                # tune timeouts per service conservatively
                timeout_map = {
                    "autonomy_engine": 30.0,
                    "memory_consolidator": 120.0,
                    "reflection_engine": 90.0,
                    "temporal_scheduler": 60.0,
                }
                timeout = timeout_map.get(svc_name, 60.0)
                # ask supervisor to watch it
                asyncio.get_event_loop().call_soon_threadsafe(asyncio.create_task, sup.watch(svc_name, coro_fac, restart=True, timeout=timeout))
            except Exception:
                logger.exception("Failed to supervise %s", svc_name)

    # wrap worker_pool submit (best-effort)
    wp = app.get("worker_pool")
    if wp and hasattr(wp, "submit") and not getattr(wp, "_supervisor_wrapped", False):
        orig_submit = wp.submit
        def wrapped_submit(fn, *args, **kwargs):
            """
            Wrap synchronous or coroutine callables submitted to worker_pool:
            - If fn is coroutine function, schedule supervised coroutine via supervisor
            - Else, call original submit (sync work) but monitor with a light timeout via supervisor wrapper
            """
            try:
                # async coroutine function
                if asyncio.iscoroutinefunction(fn):
                    factory = lambda: fn(*args, **kwargs)
                    # create a unique name for this ad-hoc job
                    svc_name = f"workerpool_task_{int(time.time()*1000)}"
                    # ask supervisor to watch it with short timeout
                    asyncio.get_event_loop().call_soon_threadsafe(asyncio.create_task, sup.watch(svc_name, factory, restart=False, timeout=30.0))
                    return True
                # sync function: submit to original and return
                return orig_submit(fn, *args, **kwargs)
            except Exception:
                logger.exception("wrapped_submit failed, falling back to original submit")
                return orig_submit(fn, *args, **kwargs)
        try:
            wp.submit = wrapped_submit
            wp._supervisor_wrapped = True
            logger.info("worker_pool.submit wrapped by supervisor")
        except Exception:
            logger.exception("Failed to wrap worker_pool.submit")

    # expose a small health check endpoint for quick calls
    def health_probe():
        try:
            return sup.health()
        except Exception:
            return {"error": "supervisor.health_failed"}
    app["supervisor_health"] = health_probe

    logger.info("Supervisor attached and watching services: %s", services)
    return sup

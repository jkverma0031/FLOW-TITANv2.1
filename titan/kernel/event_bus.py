# titan/kernel/event_bus.py
from __future__ import annotations
from typing import Callable, Dict, List, Any
from threading import RLock
from concurrent.futures import ThreadPoolExecutor, Future
import logging
import traceback
import uuid

logger = logging.getLogger(__name__)

# Lazy metrics/tracing integration (optional)
try:
    from titan.observability.metrics import metrics
except Exception:
    metrics = None

try:
    from titan.observability.tracing import tracer
except Exception:
    class _NoTracer:
        def current_trace_id(self): return None
        def current_span_id(self): return None
    tracer = _NoTracer()


class EventBus:
    """
    Thread-safe EventBus with:
      - sync and async handlers support (sync handlers run in thread pool)
      - wildcard subscriptions (prefix.*)
      - shutdown support (graceful)
      - blocking publish for critical events
    """

    def __init__(self, max_workers: int = 8):
        self._lock = RLock()
        self._subscribers: Dict[str, List[Callable[[Dict[str, Any]], Any]]] = {}
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        self._shutdown = False

    # ------------------------
    # Subscription API
    # ------------------------
    def subscribe(self, event_type: str, handler: Callable[[Dict[str, Any]], Any]) -> None:
        """
        Subscribe to a specific event type or wildcard ending with '.*' for prefix matching.
        """
        with self._lock:
            self._subscribers.setdefault(event_type, []).append(handler)
        logger.debug("Subscribed handler %s to %s", getattr(handler, "__name__", repr(handler)), event_type)

    def unsubscribe(self, event_type: str, handler: Callable[[Dict[str, Any]], Any]) -> None:
        with self._lock:
            handlers = self._subscribers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)
                logger.debug("Unsubscribed handler %s from %s", getattr(handler, "__name__", repr(handler)), event_type)

    # ------------------------
    # Publishing API
    # ------------------------
    def publish(self, event_type: str, payload: Dict[str, Any], block: bool = False, timeout: Optional[float] = None) -> None:
        """
        Publish an event. If block=True handlers are invoked synchronously (with optional timeout).
        Otherwise, handlers are executed asynchronously in a threadpool.
        Wildcard/prefix subscribers are supported.
        """
        if self._shutdown:
            logger.warning("EventBus is shutting down. Dropping event %s", event_type)
            return

        # observability: metrics & tracing (best-effort)
        try:
            if metrics:
                metrics.counter("eventbus.published").inc()
        except Exception:
            pass

        trace_info = {"trace_id": getattr(tracer, "current_trace_id", lambda: None)(), "span_id": getattr(tracer, "current_span_id", lambda: None)()}
        logger.info("Event published %s keys=%s session=%s trace=%s", event_type, list(payload.keys()), payload.get("session_id"), trace_info["trace_id"])

        # collect matching handlers (exact + prefix wildcards)
        handlers = []
        with self._lock:
            # exact match handlers
            handlers.extend(self._subscribers.get(event_type, []) or [])
            # wildcard prefix matches, e.g. 'perception.*' will match 'perception.keyboard'
            if "." in event_type:
                parts = event_type.split(".")
                for i in range(1, len(parts)):
                    prefix = ".".join(parts[:i]) + ".*"
                    handlers.extend(self._subscribers.get(prefix, []) or [])
            # also any global wildcard listeners registered as '*'
            handlers.extend(self._subscribers.get("*", []) or [])
            # deduplicate preserving order
            seen = set()
            dedup = []
            for h in handlers:
                if id(h) not in seen:
                    seen.add(id(h))
                    dedup.append(h)
            handlers = dedup

        if not handlers:
            return

        # synchronous dispatch
        if block:
            for h in handlers:
                try:
                    # allow handler to be coroutine function - run it via asyncio if necessary
                    res = h(payload)
                    if getattr(res, "__await__", None):
                        # coroutine returned; run it to completion
                        import asyncio
                        asyncio.get_event_loop().run_until_complete(res)
                except Exception:
                    logger.exception("Event handler error for %s", event_type)
            return

        # asynchronous dispatch via thread pool, each handler isolated
        for h in handlers:
            try:
                self._pool.submit(self._safe_call, h, event_type, payload)
            except Exception:
                logger.exception("Failed to submit handler to pool for event %s", event_type)

    def _safe_call(self, handler: Callable[[Dict[str, Any]], Any], event_type: str, payload: Dict[str, Any]) -> None:
        try:
            res = handler(payload)
            # if handler returns coroutine, run it in a short-lived loop (best-effort)
            if getattr(res, "__await__", None):
                try:
                    import asyncio
                    asyncio.run(res)
                except Exception:
                    # if asyncio.run fails inside thread (rare), fallback to ignoring coroutine
                    logger.debug("Handler coroutine execution failed in thread for event %s", event_type)
        except Exception:
            logger.exception("Event handler raised exception for %s", event_type)

    # ------------------------
    # Shutdown
    # ------------------------
    def shutdown(self, wait: bool = True, grace: float = 1.0):
        """
        Graceful shutdown: stop accepting new events and optionally wait for in-flight handlers.
        """
        self._shutdown = True
        if wait:
            self._pool.shutdown(wait=True)
        else:
            try:
                self._pool.shutdown(wait=False)
            except Exception:
                pass
        logger.info("EventBus shutdown complete")

# Path: FLOW/titan/kernel/event_bus.py
from __future__ import annotations
from typing import Callable, Dict, List, Any
from threading import RLock
from concurrent.futures import ThreadPoolExecutor
import logging

# Observability imports
from titan.observability.metrics import metrics
from titan.observability.tracing import tracer

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self, max_workers: int = 4):
        self._lock = RLock()
        self._subscribers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        self._pool = ThreadPoolExecutor(max_workers=max_workers)

    def subscribe(self, event_type: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        with self._lock:
            self._subscribers.setdefault(event_type, []).append(handler)

    def publish(self, event_type: str, payload: Dict[str, Any], block: bool = False) -> None:
        """
        Publish an event:
          - block=False (default): handlers run in threadpool asynchronously
          - block=True: handlers run synchronously (use for critical flows)
        """

        # -------------------------------
        # 🔎 OBSERVABILITY INSTRUMENTATION
        # -------------------------------

        # Metrics
        metrics.counter("eventbus.published").inc()

        # Logging with correlation IDs
        logger.info(
            "Event published",
            extra={
                "event_type": event_type,
                "payload_keys": list(payload.keys()),
                "session_id": payload.get("session_id"),
                "plan_id": payload.get("plan_id"),
                "node_id": payload.get("node_id"),
                "trace_id": tracer.current_trace_id(),
                "span_id": tracer.current_span_id(),
            }
        )

        # -------------------------------
        # 🔚 END OBSERVABILITY SECTION
        # -------------------------------

        with self._lock:
            handlers = list(self._subscribers.get(event_type, []))

        if not handlers:
            return

        # Synchronous dispatch
        if block:
            for h in handlers:
                try:
                    h(payload)
                except Exception:
                    logger.exception("Event handler error for %s", event_type)
            return

        # Asynchronous dispatch through thread pool
        for h in handlers:
            self._pool.submit(self._safe_call, h, payload)

    def _safe_call(self, h: Callable[[Dict[str, Any]], None], payload: Dict[str, Any]):
        try:
            h(payload)
        except Exception:
            logger.exception("Event handler raised exception")

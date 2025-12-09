# titan/observability/metrics_adapter.py
from __future__ import annotations
import logging
import time
from typing import Any, Dict

logger = logging.getLogger("titan.observability.metrics_adapter")

class NoopCounter:
    def inc(self, n: int = 1): pass
    def set(self, v): pass

class MetricsAdapter:
    """
    Lightweight metrics adapter. If a richer metrics backend exists in app (e.g. prometheus client),
    set it in app['metrics_backend'] and this adapter will try to use it. Otherwise fallback to in-memory counters.
    """

    def __init__(self, app: Dict[str, Any]):
        self.app = app
        self.backend = app.get("metrics_backend")
        self._counters = {}
        self._gauges = {}
        self._last_snapshot_ts = 0.0

    def counter(self, name: str):
        if self.backend and hasattr(self.backend, "counter"):
            try:
                return self.backend.counter(name)
            except Exception:
                logger.debug("backend.counter failed, using fallback")
        if name not in self._counters:
            self._counters[name] = 0
        class C:
            def __init__(self, parent, n):
                self.parent = parent
                self.name = n
            def inc(self, v: int = 1):
                self.parent._counters[self.name] = self.parent._counters.get(self.name, 0) + v
            def set(self, v):
                self.parent._counters[self.name] = v
        return C(self, name)

    def gauge(self, name: str):
        if name not in self._gauges:
            self._gauges[name] = 0
        class G:
            def __init__(self, parent, n):
                self.parent = parent
                self.name = n
            def set(self, v):
                self.parent._gauges[self.name] = v
        return G(self, name)

    def snapshot(self) -> Dict[str, Any]:
        return {"ts": time.time(), "counters": dict(self._counters), "gauges": dict(self._gauges)}

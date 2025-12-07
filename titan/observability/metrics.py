# Path: FLOW/titan/observability/metrics.py
from __future__ import annotations
import threading
import time
from typing import Dict, Any, List


class Counter:
    def __init__(self):
        self.value = 0
        self.lock = threading.Lock()

    def inc(self, amount: int = 1):
        with self.lock:
            self.value += amount


class Gauge:
    def __init__(self):
        self.value = 0
        self.lock = threading.Lock()

    def set(self, v: float):
        with self.lock:
            self.value = v


class Histogram:
    """
    Classic static-bucket histogram.
    """
    def __init__(self, buckets: List[float]):
        self.buckets = sorted(buckets)
        self.counts = {b: 0 for b in self.buckets}
        self.lock = threading.Lock()

    def observe(self, value: float):
        with self.lock:
            for b in self.buckets:
                if value <= b:
                    self.counts[b] += 1
                    break


class MetricsRegistry:
    """
    Global metrics registry used by TITAN subsystems.
    """
    def __init__(self):
        self.counters: Dict[str, Counter] = {}
        self.gauges: Dict[str, Gauge] = {}
        self.histograms: Dict[str, Histogram] = {}
        self.lock = threading.Lock()

    def counter(self, name: str) -> Counter:
        with self.lock:
            if name not in self.counters:
                self.counters[name] = Counter()
            return self.counters[name]

    def gauge(self, name: str) -> Gauge:
        with self.lock:
            if name not in self.gauges:
                self.gauges[name] = Gauge()
            return self.gauges[name]

    def histogram(self, name: str, buckets: List[float]) -> Histogram:
        with self.lock:
            if name not in self.histograms:
                self.histograms[name] = Histogram(buckets)
            return self.histograms[name]

    def snapshot(self) -> Dict[str, Any]:
        """
        Return a dictionary snapshot of metrics for
        API export or periodic monitoring.
        """
        out = {}

        for name, c in self.counters.items():
            with c.lock:
                out[f"{name}_count"] = c.value

        for name, g in self.gauges.items():
            with g.lock:
                out[f"{name}_gauge"] = g.value

        for name, h in self.histograms.items():
            with h.lock:
                out[f"{name}_histogram"] = dict(h.counts)

        return out

    def timer(self, name: str):
        """
        Usage:
            with metrics.timer("executor.duration"):
                executor.run(...)
        """
        registry = self

        class TimerCtx:
            def __enter__(self):
                self.start = time.time()

            def __exit__(self, exc_type, exc, tb):
                dur = time.time() - self.start
                reg_h = registry.histogram(name, buckets=[0.01, 0.05, 0.1, 0.5, 1, 2, 5])
                reg_h.observe(dur)

        return TimerCtx()


# Global Metrics
metrics = MetricsRegistry()

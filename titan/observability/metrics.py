# titan/observability/metrics.py
from __future__ import annotations
import threading
import time
from typing import Dict, Any, List

class Counter:
    def __init__(self):
        self.value = 0
        self.lock = threading.Lock()

    def inc(self, amount: int = 1) -> None:
        with self.lock:
            self.value += amount

    def get(self) -> int:
        with self.lock:
            return int(self.value)

class Gauge:
    def __init__(self):
        self.value = 0.0
        self.lock = threading.Lock()

    def set(self, v: float) -> None:
        with self.lock:
            self.value = float(v)

    def get(self) -> float:
        with self.lock:
            return float(self.value)

class Histogram:
    """
    Static-bucket histogram. Buckets must be increasing numbers.
    Observation will count the value into the first bucket >= value,
    or the last bucket if value is larger than all buckets.
    """
    def __init__(self, buckets: List[float]):
        # ensure unique sorted buckets
        self.buckets = sorted(list(dict.fromkeys(buckets)))
        self.counts = {b: 0 for b in self.buckets}
        self.lock = threading.Lock()

    def observe(self, value: float) -> None:
        with self.lock:
            for b in self.buckets:
                if value <= b:
                    self.counts[b] += 1
                    break
            else:
                # value exceeds all buckets -> increment last bucket
                if self.buckets:
                    self.counts[self.buckets[-1]] += 1

    def snapshot(self) -> Dict[float, int]:
        with self.lock:
            return dict(self.counts)

class MetricsRegistry:
    """
    Small, thread-safe metrics registry.
    """
    def __init__(self):
        self._counters: Dict[str, Counter] = {}
        self._gauges: Dict[str, Gauge] = {}
        self._histograms: Dict[str, Histogram] = {}
        self._lock = threading.Lock()

    def counter(self, name: str) -> Counter:
        with self._lock:
            if name not in self._counters:
                self._counters[name] = Counter()
            return self._counters[name]

    def gauge(self, name: str) -> Gauge:
        with self._lock:
            if name not in self._gauges:
                self._gauges[name] = Gauge()
            return self._gauges[name]

    def histogram(self, name: str, buckets: List[float]) -> Histogram:
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = Histogram(buckets)
            return self._histograms[name]

    def snapshot(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        with self._lock:
            for n, c in self._counters.items():
                out[f"{n}.count"] = c.get()
            for n, g in self._gauges.items():
                out[f"{n}.gauge"] = g.get()
            for n, h in self._histograms.items():
                out[f"{n}.histogram"] = h.snapshot()
        return out

    def timer(self, name: str, buckets: List[float] | None = None):
        """
        Context manager returned from metrics.timer(name).
        Example:
            with metrics.timer("executor.duration"):
                do_work()
        """
        registry = self
        use_buckets = buckets or [0.01, 0.05, 0.1, 0.5, 1, 2, 5]

        class TimerCtx:
            def __enter__(self_inner):
                self_inner._start = time.time()
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                dur = time.time() - self_inner._start
                hist = registry.histogram(name, use_buckets)
                hist.observe(dur)

        return TimerCtx()

# singleton
metrics = MetricsRegistry()

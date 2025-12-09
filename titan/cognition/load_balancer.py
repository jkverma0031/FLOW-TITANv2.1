# titan/cognition/load_balancer.py
"""
Cognitive Load Balancer (Balanced Mode - Moderate Throttle)

Responsibilities:
- Maintain a dynamic cognition load value (0.0 - 1.0)
- Score incoming proposals and supply allow/deny decisions
- Apply backpressure to periodic services (skills, memory consolidation, reflection)
- Emit events: 'cognition.load.changed', 'cognition.load.high', 'cognition.load.low'
- Expose lightweight runtime API:
    lb.record_event(kind="proposal"|"tick"|"io", weight=1.0)
    lb.allow_proposal(proposal_dict) -> bool
    lb.allow_service(service_name) -> bool
    lb.get_load() -> float
- Integrates with metrics_adapter if available
- Balanced Mode (moderate throttle) sensitivity preset by default
"""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger("titan.cognition.load_balancer")


class CognitiveLoadBalancerConfig:
    # Thresholds for Balanced (Moderate throttle)
    LOAD_SCALE_WINDOW = 30.0        # seconds window for smoothing
    SPREAD = 6.0                    # smoothing factor
    THRESHOLD_WARN = 0.6            # > warn
    THRESHOLD_HIGH = 0.8            # > high (apply stronger throttle)
    MIN_THROTTLE = 0.05             # minimum throttle step
    MAX_THROTTLE = 0.9              # extreme throttle cap
    PROPOSAL_BASE_WEIGHT = 1.0      # weight per proposal event
    TICK_BASE_WEIGHT = 0.5          # weight per skill tick
    IO_BASE_WEIGHT = 0.8            # weight for heavy IO work
    METRICS_UPDATE_INTERVAL = 5.0   # metrics publish interval


class CognitiveLoadBalancer:
    def __init__(self, app: Dict[str, Any], config: Optional[CognitiveLoadBalancerConfig] = None):
        self.app = app or {}
        self.config = config or CognitiveLoadBalancerConfig()
        self._score = 0.0  # raw aggregated score
        self._load = 0.0   # smoothed 0..1
        self._history = []  # list of (ts, delta_weight)
        self._lock = asyncio.Lock()
        self._last_emit_state = None
        self._last_metrics_ts = 0.0

        # integration points
        self.event_bus = self.app.get("event_bus")
        self.metrics = self.app.get("metrics_adapter")
        # expose into app
        try:
            self.app["load_balancer"] = self
        except Exception:
            pass

    # ------------------------
    # Public API
    # ------------------------
    def record_event(self, kind: str = "proposal", weight: Optional[float] = None) -> None:
        """
        Record an occurrence that should increase cognitive load.
        kind: 'proposal' | 'tick' | 'io' | custom
        """
        w = weight if weight is not None else self._default_weight_for_kind(kind)
        now = time.time()
        self._history.append((now, float(w)))
        # keep history window bounded
        cutoff = now - self.config.LOAD_SCALE_WINDOW * 2
        self._history = [h for h in self._history if h[0] >= cutoff]
        # recompute load opportunistically (sync)
        asyncio.get_event_loop().call_soon_threadsafe(asyncio.create_task, self._recompute_load())

    async def _recompute_load(self):
        async with self._lock:
            now = time.time()
            window = self.config.LOAD_SCALE_WINDOW
            # sum weights inside window with exponential decay
            total = 0.0
            for ts, w in self._history:
                age = max(0.0, now - ts)
                # exponential decay factor
                decay = pow(2.0, -age / self.config.SPREAD)
                total += w * decay
            # normalize total into 0..1 range heuristically (tunable)
            # use a soft cap based on expected load capacity
            normalized = total / max(1.0, self.config.SPREAD * 4.0)
            new_load = max(0.0, min(1.0, normalized))
            changed = abs(new_load - self._load) > 0.01
            self._load = new_load
            # emit events if crossing thresholds
            if changed:
                await self._emit_load_change(self._load)
            # periodically emit metrics
            if self.metrics and (now - self._last_metrics_ts > self.config.METRICS_UPDATE_INTERVAL):
                try:
                    self.metrics.gauge("cognition_load").set(self._load)
                    self.metrics.counter("cognition_events_recorded").inc(len(self._history))
                except Exception:
                    pass
                self._last_metrics_ts = now

    def _default_weight_for_kind(self, kind: str) -> float:
        if kind == "proposal":
            return self.config.PROPOSAL_BASE_WEIGHT
        if kind == "tick":
            return self.config.TICK_BASE_WEIGHT
        if kind == "io":
            return self.config.IO_BASE_WEIGHT
        return 0.5

    def get_load(self) -> float:
        return float(self._load)

    def allow_proposal(self, proposal: Dict[str, Any]) -> bool:
        """
        Decide whether a proposal should proceed to DecisionPolicy.
        Rules:
        - If load < warn threshold -> allow
        - If warn <= load < high -> apply soft filtering (allow lower-risk / higher-confidence)
        - If load >= high -> only allow highest priority / low risk proposals
        For fused proposals, use 'priority' or 'components' to evaluate.
        """
        # record proposal event
        try:
            self.record_event("proposal", self.config.PROPOSAL_BASE_WEIGHT)
        except Exception:
            pass

        load = self.get_load()
        if load < self.config.THRESHOLD_WARN:
            return True

        # evaluate proposal shape defensively
        risk = proposal.get("risk")
        confidence = proposal.get("confidence", 0.0)
        skill = proposal.get("skill") or proposal.get("skill_name") or proposal.get("skill_name")
        priority = proposal.get("priority", 50)

        # soft policy under moderate load
        if load < self.config.THRESHOLD_HIGH:
            # accept if low or medium risk and decent confidence or high priority
            if risk in (None, "low", "LOW", "Low") or str(risk).upper() in ("LOW", "MEDIUM"):
                if confidence >= 0.5 or priority >= 80:
                    return True
            # allow urgent proposals regardless
            if str(risk).upper() == "HIGH" and priority >= 95:
                return True
            # reject low-confidence high-risk under moderate load
            if confidence < 0.6 and str(risk).upper() == "HIGH":
                return False
            # otherwise fallback to allow (conservative)
            return False if confidence < 0.6 else True

        # heavy load: be strict
        # allow only low-risk high-confidence or very high-priority
        if str(risk).upper() in ("LOW", "") or risk is None:
            if confidence >= 0.75 or priority >= 90:
                return True
        if priority >= 98:
            return True
        # otherwise deny
        return False

    def allow_service(self, service_name: str) -> bool:
        """
        Used by background services (memory_consolidator, reflection, predictive) to decide whether to run now.
        Under moderate load, services should run less frequently or skip cycles.
        """
        self.record_event("tick", self.config.TICK_BASE_WEIGHT)
        load = self.get_load()
        # mapping of service sensitivity (conservative choices)
        sensitivities = {
            "memory_consolidator": 0.7,
            "reflection_engine": 0.65,
            "predictive_context": 0.6,
            "temporal_scheduler": 0.85,
            "skill_manager": 0.5,
        }
        sens = sensitivities.get(service_name, 0.6)
        # allow when load < sens; under heavy load allow if very infrequent (probabilistic)
        if load < sens:
            return True
        # probabilistic skip to avoid deterministic silence
        import random
        prob = max(0.0, 1.0 - (load - sens) * 2.0)
        decision = random.random() < prob
        if not decision:
            # record a heavier penalty for skipped runs
            self.record_event("io", 0.8)
        return decision

    async def _emit_load_change(self, load_val: float):
        # emit change and thresholds crossing events
        prev = self._last_emit_state or 0.0
        self._last_emit_state = load_val
        try:
            if self.event_bus and getattr(self.event_bus, "publish", None):
                self.event_bus.publish("cognition.load.changed", {"ts": time.time(), "load": load_val})
                if load_val >= self.config.THRESHOLD_HIGH:
                    self.event_bus.publish("cognition.load.high", {"ts": time.time(), "load": load_val})
                elif load_val <= self.config.THRESHOLD_WARN * 0.8 and prev >= self.config.THRESHOLD_WARN:
                    # de-escalation
                    self.event_bus.publish("cognition.load.low", {"ts": time.time(), "load": load_val})
        except Exception:
            logger.debug("Failed to publish load events")

    # ------------------------
    # Utilities
    # ------------------------
    def snapshot(self) -> Dict[str, Any]:
        return {"ts": time.time(), "load": self.get_load(), "history_len": len(self._history)}

# titan/stability/harness.py
"""
Stability Test Harness

Provides utilities to:
- simulate bursts of perception events (keyboard/mouse/transcript/notification)
- simulate skill proposals and fused proposals
- measure proposals/sec, throttles, load and latency
- run smoke sequences (short), stress sequences (larger)
- produce a test report (counts + durations)

Usage:
  from titan.stability.harness import StabilityHarness
  h = StabilityHarness(app)
  h.run_smoke_test(duration_seconds=10)
  report = h.get_report()
"""

from __future__ import annotations
import asyncio
import time
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("titan.stability.harness")

class StabilityHarness:
    def __init__(self, app: Dict[str, Any]):
        self.app = app or {}
        self.event_bus = self.app.get("event_bus")
        self.load_balancer = self.app.get("load_balancer")
        self.reports: Dict[str, Any] = {}
        self._running = False

    async def _emit_event(self, topic: str, payload: Dict[str, Any]):
        try:
            if self.event_bus and getattr(self.event_bus, "publish", None):
                self.event_bus.publish(topic, payload)
            else:
                # best-effort: if no event bus, call skill_manager's hooks if exist
                sm = self.app.get("skill_manager")
                if sm and getattr(sm, "inject_event", None):
                    sm.inject_event(topic, payload)
        except Exception:
            logger.exception("emit_event failed")

    async def _burst_proposals(self, rate_per_sec: int, duration: int):
        """
        Emit proposals at specified rate for duration seconds
        """
        total = 0
        start = time.time()
        interval = 1.0 / max(1.0, rate_per_sec)
        while time.time() - start < duration:
            payload = {"type": "skill.proposal", "proposal": {"intent": "test_proposal", "confidence": 0.6, "risk": "low", "priority": 50, "skill": "harness_sim"}}
            await self._emit_event("skill.proposal", payload)
            total += 1
            await asyncio.sleep(interval)
        return total

    async def _burst_perception(self, rate_per_sec: int, duration: int):
        total = 0
        start = time.time()
        interval = 1.0 / max(1.0, rate_per_sec)
        while time.time() - start < duration:
            evt = {"type": "perception.keyboard", "ts": time.time(), "payload": {"text": "user typing"}}
            await self._emit_event("perception.keyboard", evt)
            total += 1
            await asyncio.sleep(interval)
        return total

    async def run_test(self, mode: str = "smoke", duration: int = 10, proposal_rate: int = 20, perception_rate: int = 10):
        """
        mode: 'smoke' or 'stress'
        smoke: moderate load
        stress: heavier and longer
        """
        self._running = True
        start = time.time()
        results = {"mode": mode, "start": start, "duration_requested": duration, "proposal_sent": 0, "perception_sent": 0, "throttled_events": 0}
        # subscribe to throttled events count if possible
        throttled_cnt = 0
        def _on_throttle(evt):
            nonlocal throttled_cnt
            throttled_cnt += 1
        if self.event_bus and getattr(self.event_bus, "subscribe", None):
            try:
                self.event_bus.subscribe("cognition.proposal.throttled", _on_throttle)
            except Exception:
                pass

        # run parallel bursts
        tasks = [
            asyncio.create_task(self._burst_proposals(proposal_rate, duration)),
            asyncio.create_task(self._burst_perception(perception_rate, duration))
        ]
        done = await asyncio.gather(*tasks, return_exceptions=True)
        results["proposal_sent"] = int(done[0] if isinstance(done[0], int) else 0)
        results["perception_sent"] = int(done[1] if isinstance(done[1], int) else 0)
        results["throttled_events"] = throttled_cnt
        results["end"] = time.time()
        results["elapsed"] = results["end"] - start
        # store report
        key = f"{mode}_{int(start)}"
        self.reports[key] = results
        self._running = False
        return results

    def run_smoke_test(self, duration_seconds: int = 10, proposal_rate: int = 20, perception_rate: int = 10):
        return asyncio.get_event_loop().run_until_complete(self.run_test("smoke", duration_seconds, proposal_rate, perception_rate))

    def run_stress_test(self, duration_seconds: int = 60, proposal_rate: int = 200, perception_rate: int = 50):
        return asyncio.get_event_loop().run_until_complete(self.run_test("stress", duration_seconds, proposal_rate, perception_rate))

    def get_report(self, key: Optional[str] = None):
        if key:
            return self.reports.get(key)
        return dict(self.reports)

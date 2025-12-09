# titan/cognition/cognitive_loop.py
"""
Unified Cognitive Loop (Titan v2.1)

This module creates a single orchestrated cognitive cycle
that runs as Titan’s "Heartbeat".

Cycle order:
    1. perception_tick()
    2. skill_manager.tick_all()
    3. cross_skill_reasoner.fuse()
    4. predictive_context.recommend()
    5. autonomy_engine.step()
    6. reflection_engine.run_once()
    7. memory_consolidator.consolidate_once()

Every cycle:
    - consults load_balancer
    - consults supervisor health
    - emits cognitive.cycle events
    - dynamically adjusts timing
"""

from __future__ import annotations
import asyncio
import time
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("titan.cognition.cognitive_loop")


class CognitiveLoopConfig:
    BASE_INTERVAL = 1.0            # 1 second heartbeat
    MIN_INTERVAL = 0.3             # don’t go faster
    MAX_INTERVAL = 5.0             # don’t go slower
    REFLECTION_INTERVAL = 90       # every X cycles
    MEMORY_INTERVAL = 60           # every X cycles
    FUSION_INTERVAL = 1            # every cycle
    PREDICT_INTERVAL = 2           # every 2 cycles


class CognitiveLoop:
    def __init__(self, app: Dict[str, Any], config: Optional[CognitiveLoopConfig] = None):
        self.app = app
        self.config = config or CognitiveLoopConfig()

        # Components provided by app
        self.load_balancer = app.get("load_balancer")
        self.supervisor = app.get("supervisor")
        self.skill_manager = app.get("skill_manager")
        self.cross_reasoner = app.get("cross_skill_reasoner")
        self.predictive = app.get("predictive_context")
        self.autonomy_engine = app.get("autonomy_engine")
        self.reflection = app.get("reflection_engine")
        self.memory = app.get("memory_consolidator")
        self.perception = app.get("perception_manager")
        self.event_bus = app.get("event_bus")
        self.metrics = app.get("metrics_adapter")

        self._running = False
        self._task = None
        self._cycle_count = 0

        try:
            self.app["cognitive_loop"] = self
        except Exception:
            pass

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("CognitiveLoop started")

    async def stop(self):
        self._running = False
        if self._task:
            try:
                self._task.cancel()
                await self._task
            except Exception:
                pass

    async def _loop(self):
        interval = self.config.BASE_INTERVAL

        while self._running:
            cycle_start = time.time()
            self._cycle_count += 1

            # --------------------------------
            # 0. Supervisor Health Check
            # --------------------------------
            if self.supervisor:
                health = self.supervisor.health()
                # If major service is dead, slow cycles dramatically
                if any(v.get("dead") for v in health["services"].values()):
                    interval = min(self.config.MAX_INTERVAL, interval + 1.0)
                # record metrics
                if self.metrics:
                    self.metrics.gauge("supervisor_services_dead").set(
                        sum(1 for v in health["services"].values() if v.get("dead"))
                    )

            # --------------------------------
            # 1. Perception Tick
            # --------------------------------
            if self.perception and self._permit("perception"):
                try:
                    r = self.perception.tick()
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:
                    logger.exception("perception.tick failed")

            # --------------------------------
            # 2. Skill Processing
            # --------------------------------
            if self.skill_manager and self._permit("skills"):
                try:
                    await self.skill_manager.tick_all()
                except Exception:
                    logger.exception("skill_manager.tick_all failed")

            # --------------------------------
            # 3. Cross-Skill Fusion
            # --------------------------------
            if (
                self.cross_reasoner
                and self._permit("fusion")
                and (self._cycle_count % self.config.FUSION_INTERVAL == 0)
            ):
                try:
                    await self.cross_reasoner.fuse()
                except Exception:
                    logger.exception("cross_reasoner fuse failed")

            # --------------------------------
            # 4. Predictive Context
            # --------------------------------
            if (
                self.predictive
                and self._permit("predict")
                and (self._cycle_count % self.config.PREDICT_INTERVAL == 0)
            ):
                try:
                    await self.predictive.recommend({})
                except Exception:
                    logger.exception("predictive recommend failed")

            # --------------------------------
            # 5. Autonomy Step (Core agent action)
            # --------------------------------
            if self.autonomy_engine and self._permit("autonomy"):
                try:
                    await self.autonomy_engine.step()
                except Exception:
                    logger.exception("autonomy_engine.step failed")

            # --------------------------------
            # 6. Reflection Engine
            # --------------------------------
            if (
                self.reflection
                and (self._cycle_count % self.config.REFLECTION_INTERVAL == 0)
                and self._permit("reflection_engine")
            ):
                try:
                    await self.reflection.run_once()
                except Exception:
                    logger.exception("reflection failed")

            # --------------------------------
            # 7. Memory Consolidation
            # --------------------------------
            if (
                self.memory
                and (self._cycle_count % self.config.MEMORY_INTERVAL == 0)
                and self._permit("memory_consolidator")
            ):
                try:
                    await self.memory.consolidate_once()
                except Exception:
                    logger.exception("memory consolidation failed")

            # --------------------------------
            # 8. Load Balancer Feedback
            # --------------------------------
            if self.load_balancer and self.metrics:
                self.metrics.gauge("cognitive_load").set(self.load_balancer.get_load())

            # --------------------------------
            # 9. Emit cycle event
            # --------------------------------
            if self.event_bus:
                try:
                    self.event_bus.publish(
                        "cognition.cycle",
                        {
                            "ts": time.time(),
                            "cycle": self._cycle_count,
                            "interval": interval,
                            "load": self.load_balancer.get_load() if self.load_balancer else None,
                        },
                    )
                except Exception:
                    pass

            # --------------------------------
            # 10. Dynamic pacing
            # --------------------------------
            if self.load_balancer:
                load = self.load_balancer.get_load()
                if load > 0.8:
                    interval = min(self.config.MAX_INTERVAL, interval + 0.3)
                elif load < 0.3:
                    interval = max(self.config.MIN_INTERVAL, interval - 0.2)

            # sleep until next cycle
            elapsed = time.time() - cycle_start
            sleep_for = max(0.05, interval - elapsed)
            try:
                await asyncio.sleep(sleep_for)
            except Exception:
                pass

    # ----------------------------
    # Permission logic (Load-aware)
    # ----------------------------
    def _permit(self, component: str) -> bool:
        if not self.load_balancer:
            return True
        return self.load_balancer.allow_service(component)

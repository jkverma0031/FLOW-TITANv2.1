# titan/autonomy/skills/reflection_skill.py
"""
ReflectionSkill

A skill wrapper around ReflectionEngine so reflection can be treated as a skill:
- enable/disable via SkillManager
- tick-driven (periodic)
- can be asked to run a manual reflection (via skill_manager context.publish_event or an API)
- publishes a small summary event 'skill.reflection.summary' that other parts of Titan can subscribe to
"""

from __future__ import annotations
import asyncio
import logging
import time
from typing import Dict, Any, Optional

from .base import BaseSkill

# import the engine (assumes titan.cognition.reflection_engine present)
try:
    from titan.cognition.reflection_engine import ReflectionEngine, ReflectionConfig
    HAS_ENGINE = True
except Exception:
    ReflectionEngine = None
    ReflectionConfig = None
    HAS_ENGINE = False

logger = logging.getLogger("titan.skills.reflection_skill")


class ReflectionSkill(BaseSkill):
    NAME = "reflection_skill"
    DESCRIPTION = "Performs meta-cognitive reflection on Titan's recent episodes and surfaces lessons."
    TICK_INTERVAL = 600.0  # default 10 minutes; SkillManager tick respects per-skill TICK_INTERVAL
    COOLDOWN = 120.0
    SUBSCRIPTIONS = ("perception.*", "autonomy.ask_user_confirmation", "skill.proposal", "skill.fused_proposal")

    async def on_start(self) -> None:
        await super().on_start()
        self._engine: Optional[ReflectionEngine] = None
        # use engine on app if present, else create one
        app = getattr(self.manager, "app", {}) if hasattr(self.manager, "app") else {}
        try:
            if app.get("reflection_engine"):
                self._engine = app["reflection_engine"]
            elif HAS_ENGINE:
                cfg = ReflectionConfig(tick_interval=self.TICK_INTERVAL, lookback_seconds=self.persistent_state.metadata.get("lookback_seconds", 24 * 3600))
                self._engine = ReflectionEngine(app, config=cfg)
                try:
                    # attach into app for reuse
                    app["reflection_engine"] = self._engine
                except Exception:
                    pass
            # start engine service (non-blocking)
            if self._engine:
                coro = self._engine.start()
                if asyncio.iscoroutine(coro):
                    asyncio.create_task(coro)
            self.logger.info("ReflectionSkill started (engine present=%s)", bool(self._engine))
        except Exception:
            self.logger.exception("ReflectionSkill failed to start engine")

    async def tick(self, ctx) -> None:
        """
        Periodic tick triggers a reflection run (run_once) - does not block beyond timeout.
        """
        if not self.allowed_to_act():
            return
        if not self._engine:
            return
        try:
            coro = self._engine.run_once()
            # guard execution time (don't hog the tick loop)
            res = await asyncio.wait_for(coro, timeout=60.0)
            # publish a short summary for UI / observer components
            try:
                # choose top N reflections to publish
                recent = await self._engine.get_recent_reflections(limit=3)
                for r in recent:
                    payload = {"type": "skill.reflection.summary", "source": "skill", "reflection": r, "skill": self.NAME, "ts": time.time()}
                    try:
                        await ctx.publish_event(payload)
                    except Exception:
                        # best-effort: if ctx.publish_event is sync, call event_bus directly
                        try:
                            eb = getattr(self.manager, "event_bus", None) or self.manager.app.get("event_bus")
                            if eb and getattr(eb, "publish", None):
                                eb.publish("skill.reflection.summary", payload)
                        except Exception:
                            pass
            except Exception:
                logger.debug("ReflectionSkill tick: publishing summaries failed")
        except asyncio.TimeoutError:
            logger.warning("ReflectionSkill: reflection run timed out")
        except Exception:
            logger.exception("ReflectionSkill.tick failed")

    async def on_event(self, event: Dict[str, Any], ctx) -> None:
        """
        The skill listens to wide events to include them in episodic logs (already handled by other code).
        It also supports a manual trigger: if it receives an event with type 'skill.reflection.run' and source 'user' it will run once.
        """
        try:
            typ = event.get("type") or event.get("topic") or ""
            if typ == "skill.reflection.run" and event.get("source") in ("user", "cli", "api"):
                # manual run now
                if not self.allowed_to_act():
                    return
                if self._engine:
                    try:
                        await self._engine.run_once()
                    except Exception:
                        logger.exception("Manual reflection run failed")
        except Exception:
            logger.exception("ReflectionSkill.on_event failed")

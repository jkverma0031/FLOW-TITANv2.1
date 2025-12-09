# titan/autonomy/skills/memory_consolidation_skill.py
"""
Skill wrapper for Memory Consolidator so consolidation can be enabled/disabled like other skills.
This skill uses the MemoryConsolidator service (titan.cognition.memory_consolidator).
If the service is not present, it instantiates it locally using app context.
"""
from __future__ import annotations
import logging
import asyncio
import time
from typing import Dict, Any, Optional

from .base import BaseSkill
from ...cognition.memory_consolidator import MemoryConsolidator, ConsolidationConfig

logger = logging.getLogger("titan.skills.memory_consolidation")

class MemoryConsolidationSkill(BaseSkill):
    NAME = "memory_consolidation_skill"
    DESCRIPTION = "Periodic memory consolidation into semantic vector store."
    TICK_INTERVAL = 300.0  # default every 5 minutes; Service itself can tick faster.
    COOLDOWN = 120.0

    async def on_start(self):
        await super().on_start()
        self._consolidator: Optional[MemoryConsolidator] = None
        app = getattr(self.manager, "app", {}) if hasattr(self.manager, "app") else {}
        try:
            # if an external consolidator service is registered, reuse it
            if app.get("memory_consolidator"):
                self._consolidator = app["memory_consolidator"]
            else:
                # create local consolidator bound to app
                cfg = ConsolidationConfig(tick_interval=self.TICK_INTERVAL)
                self._consolidator = MemoryConsolidator(app, config=cfg)
                # attach into app for future reuse
                try:
                    app["memory_consolidator"] = self._consolidator
                except Exception:
                    pass
            # start underlying consolidator (non-blocking)
            if self._consolidator:
                coro = self._consolidator.start()
                if asyncio.iscoroutine(coro):
                    asyncio.create_task(coro)
            self.logger.info("MemoryConsolidationSkill started consolidator")
        except Exception:
            self.logger.exception("Failed to start consolidator")

    async def tick(self, ctx):
        # Optionally force a consolidation run if allowed
        try:
            if not self.allowed_to_act():
                return
            if not self._consolidator:
                return
            # run one consolidation pass in the background
            try:
                coro = self._consolidator.consolidate_once()
                if asyncio.iscoroutine(coro):
                    await asyncio.wait_for(coro, timeout=60.0)
                self.persistent_state.touch_action()
                self.save_persistent()
                self.mark_action()
            except asyncio.TimeoutError:
                logger.warning("Memory consolidation run timed out")
            except Exception:
                logger.exception("Memory consolidation run failed")
        except Exception:
            logger.exception("tick failed in MemoryConsolidationSkill")

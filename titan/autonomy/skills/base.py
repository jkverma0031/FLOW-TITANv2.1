# titan/autonomy/skills/base.py
"""
BaseSkill - canonical base class for all Skills.

A Skill is:
 - event-driven and/or periodically-ticking,
 - has a priority, cooldown, and optional required context,
 - can ask the Planner to produce a plan via SkillContext.
"""

from __future__ import annotations
import asyncio
import logging
import time
from typing import Any, Dict, Iterable, Optional, Callable, Coroutine

logger = logging.getLogger("titan.skills.base")

class SkillContext:
    """
    Lightweight context object injected into skills when tick/on_event is called.
    It exposes:
      - publish_event(event_dict)            (async)
      - query_memory(query, k=3)             (async)
      - plan_with_dsl(dsl_text) -> Plan     (async)
      - execute_plan(plan) -> result         (async)
      - get/set transient context (sync)
    Concrete callables are provided by SkillManager when creating SkillContext instances.
    """
    def __init__(
        self,
        publish_event: Callable[[Dict[str,Any]], Coroutine[Any,Any,Any]],
        query_memory: Callable[[str,int], Coroutine[Any,Any,Any]],
        plan_with_dsl: Callable[[str], Coroutine[Any,Any,Any]],
        execute_plan: Callable[[Any], Coroutine[Any,Any,Any]],
        runtime_get: Callable[[str,Any], Any],
        runtime_set: Callable[[str,Any], None],
        session_id: Optional[str] = None,
    ):
        self.publish_event = publish_event
        self.query_memory = query_memory
        self.plan_with_dsl = plan_with_dsl
        self.execute_plan = execute_plan
        self.runtime_get = runtime_get
        self.runtime_set = runtime_set
        self.session_id = session_id

class BaseSkill:
    """
    Core Skill contract. Subclass this to implement a skill.
    Key lifecycle methods:
      - on_start()  # optional setup
      - on_stop()   # optional cleanup
      - on_event(event, ctx)  # called for incoming events the skill subscribed to
      - tick(ctx)  # periodic tick, can be long-running but should not block
    """

    NAME: str = "base_skill"
    DESCRIPTION: str = "Base skill - override"
    # seconds between automatic ticks (None means no periodic tick)
    TICK_INTERVAL: Optional[float] = None
    # event topics (glob-style) this skill wants to subscribe to (e.g. ["perception.*", "events.*"])
    SUBSCRIPTIONS: Iterable[str] = ()
    # priority for conflict/resolution; higher means more important
    PRIORITY: int = 50
    # cooldown in seconds between actions that produce visible user actions (to avoid spam)
    COOLDOWN: float = 5.0

    def __init__(self, manager: "SkillManager"):
        self.manager = manager
        self._last_action_at: float = 0.0
        self._running = False
        # optional per-skill transient state store (persisted by manager if configured)
        self.state: Dict[str, Any] = {}
        self.logger = logging.getLogger(f"titan.skills.{self.NAME}")

    async def on_start(self) -> None:
        """Called once when SkillManager starts the skill (override if needed)."""
        self._running = True
        self.logger.debug("Skill %s started", self.NAME)

    async def on_stop(self) -> None:
        """Called once when SkillManager is stopping the skill."""
        self._running = False
        self.logger.debug("Skill %s stopped", self.NAME)

    async def on_event(self, event: Dict[str, Any], ctx: SkillContext) -> None:
        """
        Called for subscribed events.
        Override to inspect event and optionally call ctx.plan_with_dsl/ctx.publish_event/ctx.execute_plan.
        Keep this method non-blocking — long work should be scheduled as tasks.
        """
        return None

    async def tick(self, ctx: SkillContext) -> None:
        """
        Periodic work. Default is no-op.
        If TICK_INTERVAL is set on the class, SkillManager will periodically call this.
        """
        return None

    def allowed_to_act(self) -> bool:
        """
        Basic cooldown logic for visible actions; skills can override.
        """
        now = time.time()
        return (now - self._last_action_at) >= self.COOLDOWN

    def mark_action(self) -> None:
        self._last_action_at = time.time()

    def schedule_background(self, coro: Coroutine[Any,Any,Any]) -> None:
        """
        Helper to schedule background work on manager's loop.
        """
        try:
            self.manager._schedule_background(coro)
        except Exception:
            self.logger.exception("Failed to schedule background task")

    # helpers to access manager components conveniently
    @property
    def event_bus(self):
        return self.manager.event_bus

    @property
    def policy_engine(self):
        return self.manager.policy_engine

    @property
    def planner(self):
        return self.manager.planner

    @property
    def orchestrator(self):
        return self.manager.orchestrator

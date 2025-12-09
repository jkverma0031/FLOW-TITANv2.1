# titan/autonomy/skills/desktop_awareness.py
"""
Desktop Awareness Skill
 - Monitors active windows, idle time and notifications and suggests helpful actions.
 - Builds small DSL snippets for planner; uses safe escaping & truncation.
"""

from __future__ import annotations
import logging
import asyncio
import html
from typing import Dict, Any, Optional

from .base import BaseSkill

logger = logging.getLogger("titan.skills.desktop_awareness")

class DesktopAwarenessSkill(BaseSkill):
    NAME = "desktop_awareness"
    DESCRIPTION = "Monitors active windows, idle time and notifications and suggests helpful actions."
    TICK_INTERVAL = 5.0  # run every 5 seconds
    SUBSCRIPTIONS = ("perception.active_window", "perception.notification", "perception.transcript", "perception.idle")
    PRIORITY = 80
    COOLDOWN = 30.0  # don't prompt the user more than once every 30s

    async def on_start(self) -> None:
        await super().on_start()
        self.logger.info("DesktopAwarenessSkill initialized")
        self.state.setdefault("last_window", None)
        self.state.setdefault("idle_since", None)
        self.state.setdefault("last_prompt_at", 0.0)

    async def on_event(self, event: Dict[str, Any], ctx):
        typ = event.get("type") or event.get("topic") or ""
        try:
            if typ == "perception.active_window":
                win = event.get("payload", {}).get("window")
                if win:
                    self.state["last_window"] = win
            elif typ == "perception.idle":
                idle_secs = event.get("payload", {}).get("idle_seconds", 0)
                if idle_secs and idle_secs > 60:
                    # store monotonic timestamp when possible
                    now_mono = asyncio.get_event_loop().time()
                    self.state["idle_since"] = event.get("payload", {}).get("mono_ts") or event.get("payload", {}).get("ts") or now_mono
            elif typ == "perception.notification":
                payload = event.get("payload", {})
                app = (payload.get("app") or "").lower()
                if any(k in app for k in ("whatsapp", "slack", "message")):
                    await self._offer_read_notification(event, ctx)
        except Exception:
            self.logger.exception("on_event failed in DesktopAwarenessSkill")

    async def tick(self, ctx):
        last_win = self.state.get("last_window") or {}
        idle_since = self.state.get("idle_since")
        loop_time = asyncio.get_event_loop().time()
        try:
            # avoid disturbing if user is coding
            if last_win and "app" in last_win and "code" in (last_win.get("app") or "").lower():
                self.logger.debug("User in code editor; deferring prompts")
                return

            if idle_since:
                try:
                    elapsed = loop_time - float(idle_since)
                except Exception:
                    # fallback: skip auto actions if we can't compute elapsed
                    elapsed = 0
                if elapsed > 300 and self.allowed_to_act():
                    dsl = (
                        "t1 = task(name='memory', action='query_recent', limit=10)\n"
                        "t2 = task(name='planner', action='summarize_activity', input=t1.result)\n"
                        "t3 = task(name='notify', action='desktop_notify', message=t2.result.summary)\n"
                    )
                    try:
                        plan = await ctx.plan_with_dsl(dsl)
                        if plan:
                            await ctx.execute_plan(plan)
                            self.mark_action()
                    except Exception:
                        self.logger.exception("Failed to create/execute summary plan")
        except Exception:
            self.logger.exception("tick failed in DesktopAwarenessSkill")

    async def _offer_read_notification(self, event, ctx):
        if not self.allowed_to_act():
            return
        notif = event.get("payload", {})
        text = notif.get("text") or notif.get("title") or "You have a new message."
        # escape and truncate safely for inclusion in DSL
        safe_text = html.escape(str(text))[:180]
        dsl = (
            "t1 = task(name='ui', action='compose_notification', text=\""
            + safe_text.replace('"', '\\"')
            + "\")\n"
            "t2 = task(name='notify', action='desktop_notify', message=t1.result)\n"
        )
        try:
            plan = await ctx.plan_with_dsl(dsl)
            if plan:
                await ctx.execute_plan(plan)
                self.mark_action()
        except Exception:
            self.logger.exception("desktop awareness notify plan failed")

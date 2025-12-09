# titan/autonomy/skills/desktop_awareness.py
"""
Desktop Awareness Skill (v1.1)
 - Monitors active windows, idle time and notifications and *proposes* helpful actions
   by publishing SkillProposal events (topic 'skill.proposal').
 - Does NOT execute plans directly; the AutonomyEngine will evaluate proposals and decide.
"""

from __future__ import annotations
import logging
import asyncio
import html
import time
from typing import Dict, Any, Optional

from .base import BaseSkill
from .proposal import SkillProposal, RiskLevel

logger = logging.getLogger("titan.skills.desktop_awareness")

class DesktopAwarenessSkill(BaseSkill):
    NAME = "desktop_awareness"
    DESCRIPTION = "Monitors active windows and notifications and proposes helpful actions."
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
        self.state.setdefault("ask_first_mode", False)  # per-skill override if you want

    async def on_event(self, event: Dict[str, Any], ctx):
        typ = event.get("type") or event.get("topic") or ""
        try:
            if typ == "perception.active_window":
                win = event.get("payload", {}).get("window") or event.get("window")
                if win:
                    self.state["last_window"] = win
            elif typ == "perception.idle":
                idle_secs = event.get("payload", {}).get("idle_seconds", 0) if event.get("payload") else 0
                if idle_secs and idle_secs > 60:
                    now_mono = asyncio.get_event_loop().time()
                    self.state["idle_since"] = event.get("payload", {}).get("mono_ts") or event.get("payload", {}).get("ts") or now_mono
            elif typ == "perception.notification":
                payload = event.get("payload", {}) or {}
                app = (payload.get("app") or "").lower()
                # only propose lightweight notifications for messaging apps
                if any(k in app for k in ("whatsapp", "slack", "message", "telegram")):
                    await self._propose_read_notification(event, ctx)
        except Exception:
            self.logger.exception("on_event failed in DesktopAwarenessSkill")

    async def tick(self, ctx):
        """
        Periodic checks: if user has been idle or has shifted contexts,
        propose a helpful summary action rather than execute directly.
        """
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
                    elapsed = 0
                if elapsed > 300 and self.allowed_to_act():
                    # Propose a "summarize recent activity" intent
                    proposal = SkillProposal(
                        skill_name=self.NAME,
                        intent="summarize_recent_activity",
                        confidence=0.85,
                        params={"idle_seconds": elapsed, "last_window": last_win},
                        risk=RiskLevel.LOW,
                        timestamp=time.time(),
                    )
                    # publish proposal via SkillManager -> EventBus (skill context publish_event expected)
                    try:
                        await ctx.publish_event({"type": "skill.proposal", "source": "skill", "proposal": proposal.model_dump() if hasattr(proposal, "model_dump") else proposal.__dict__, "skill": self.NAME, "ts": time.time()})
                        self.logger.debug("Published skill.proposal summarize_recent_activity")
                        self.mark_action()
                    except Exception:
                        self.logger.exception("Failed to publish skill.proposal from tick")
        except Exception:
            self.logger.exception("tick failed in DesktopAwarenessSkill")

    async def _propose_read_notification(self, event, ctx):
        """
        When a message-like notification arrives, produce a lightweight 'read_notification' proposal.
        The engine will decide whether to ask the user or act.
        """
        if not self.allowed_to_act():
            return
        notif = event.get("payload", {}) or {}
        text = notif.get("text") or notif.get("title") or "You have a new message."
        safe_text = html.escape(str(text))[:300]
        # Prepare proposal
        proposal = SkillProposal(
            skill_name=self.NAME,
            intent="read_notification",
            confidence=0.9,
            params={"app": notif.get("app"), "text_snippet": safe_text},
            risk=RiskLevel.LOW,
            timestamp=time.time(),
        )
        try:
            await ctx.publish_event({"type": "skill.proposal", "source": "skill", "proposal": proposal.model_dump() if hasattr(proposal, "model_dump") else proposal.__dict__, "skill": self.NAME, "ts": time.time()})
            self.logger.debug("Published skill.proposal read_notification")
            self.mark_action()
        except Exception:
            self.logger.exception("Failed to publish read_notification proposal")

# titan/autonomy/skills/web_summary_skill.py
"""
WebSummarySkill (enterprise-grade v1)
- Detects browsing context (active window indicates a browser).
- When user dwells on a page or idle after browsing, propose summarization/search tasks.
- Produces proposals:
    - summarize_page (low/medium risk)
    - extract_key_points (medium risk)
    - follow_up_research (medium/high depending on query)
- Designed to avoid fetching content itself; instead proposes tasks that the Planner/Orchestrator will
  carry out using the HTTP/Browser plugin (keeps skill low-privilege).
"""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Dict, Any, Optional

from .base import BaseSkill
from .proposal import SkillProposal, RiskLevel

logger = logging.getLogger("titan.skills.web_summary")

try:
    from titan.observability.metrics import metrics  # type: ignore
except Exception:
    metrics = None

class WebSummarySkill(BaseSkill):
    NAME = "web_summary_skill"
    DESCRIPTION = "Detects when the user is browsing and proposes page summaries or research tasks."
    TICK_INTERVAL = 6.0
    SUBSCRIPTIONS = ("perception.active_window", "perception.idle", "perception.transcript")
    PRIORITY = 70
    COOLDOWN = 60.0

    BROWSER_KEYWORDS = ("chrome", "firefox", "safari", "edge", "brave", "chromium", "browser")

    async def on_start(self) -> None:
        await super().on_start()
        self.persistent_state.metadata.setdefault("last_summary_ts", 0.0)
        self.persistent_state.metadata.setdefault("recent_urls", [])
        self.save_persistent()

    async def on_event(self, event: Dict[str, Any], ctx) -> None:
        try:
            typ = event.get("type") or event.get("topic") or ""
            if typ == "perception.active_window":
                win = event.get("payload", {}).get("window") or event.get("window") or {}
                title = (win.get("title") or "")[:400]
                app = (win.get("app") or "") or (win.get("process") or "")
                active_url = (win.get("url") or "")  # some window monitors provide url
                # store volatile last active window
                try:
                    ctx.set_volatile("last_window_title", title)
                    ctx.set_volatile("last_window_app", app)
                    if active_url:
                        ctx.set_volatile("last_window_url", active_url)
                except Exception:
                    pass

            elif typ == "perception.idle":
                # idle event may trigger a summary if last window was a browser and dwell time exceeded threshold
                idle_seconds = (event.get("payload") or {}).get("idle_seconds", 0) if event.get("payload") else 0
                if idle_seconds >= 20:
                    last_app = ctx.get_volatile("last_window_app", "")
                    last_url = ctx.get_volatile("last_window_url", "")
                    if last_app and any(k in last_app.lower() for k in self.BROWSER_KEYWORDS) and last_url:
                        # propose summarization (do not fetch here)
                        if not self.allowed_to_act():
                            return
                        # guard: avoid duplicate proposals for same URL within cooldown
                        last_summary = self.persistent_state.metadata.get("last_summary_ts", 0)
                        if time.time() - last_summary < self.COOLDOWN:
                            return
                        proposal = SkillProposal(
                            skill_name=self.NAME,
                            intent="summarize_page",
                            confidence=0.9,
                            params={"url": last_url, "title": ctx.get_volatile("last_window_title", "")},
                            risk=RiskLevel.MEDIUM,
                            timestamp=time.time(),
                        )
                        await ctx.publish_event({"type": "skill.proposal", "source": "skill", "proposal": proposal.model_dump() if hasattr(proposal, "model_dump") else proposal.__dict__, "skill": self.NAME, "ts": time.time()})
                        # record
                        self.persistent_state.metadata["last_summary_ts"] = time.time()
                        self.persistent_state.metadata.setdefault("recent_urls", []).insert(0, last_url)
                        if len(self.persistent_state.metadata["recent_urls"]) > 20:
                            self.persistent_state.metadata["recent_urls"] = self.persistent_state.metadata["recent_urls"][:20]
                        self.persistent_state.touch_action()
                        self.save_persistent()
                        self.mark_action()
                        if metrics:
                            metrics.counter("skill.websummary.proposals_total").inc()
        except Exception:
            logger.exception("WebSummarySkill.on_event failed")

    async def tick(self, ctx) -> None:
        # Periodic sanity checks: if user is actively browsing and page length seems large, propose extract_key_points
        try:
            last_url = ctx.get_volatile("last_window_url", "")
            if last_url and self.allowed_to_act():
                # Heuristic: if we haven't summarized this URL recently, propose extraction
                recent = self.persistent_state.metadata.get("recent_urls", [])
                if last_url not in recent:
                    # gentle proposal, low confidence
                    proposal = SkillProposal(
                        skill_name=self.NAME,
                        intent="extract_key_points",
                        confidence=0.65,
                        params={"url": last_url},
                        risk=RiskLevel.MEDIUM,
                        timestamp=time.time(),
                    )
                    await ctx.publish_event({"type": "skill.proposal", "source": "skill", "proposal": proposal.model_dump() if hasattr(proposal, "model_dump") else proposal.__dict__, "skill": self.NAME, "ts": time.time()})
                    # record
                    self.persistent_state.metadata.setdefault("recent_urls", []).insert(0, last_url)
                    self.persistent_state.touch_action()
                    self.save_persistent()
                    self.mark_action()
                    if metrics:
                        metrics.counter("skill.websummary.periodic_proposals").inc()
        except Exception:
            logger.exception("WebSummarySkill.tick failed")

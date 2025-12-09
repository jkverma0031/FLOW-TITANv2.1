# titan/autonomy/skills/task_continuation_skill.py
"""
TaskContinuationSkill (enterprise-grade v1)
- Looks in episodic_store / memory for incomplete tasks and outstanding plans.
- Proposes continuation tasks, reminders, or plan resumption.
- Uses conservative heuristics to avoid spamming user.
- Example proposals:
    - continue_task (medium risk)
    - remind_about_task (low risk)
    - resume_plan (medium/high depending on side-effects)
"""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Dict, Any, Optional, List

from .base import BaseSkill
from .proposal import SkillProposal, RiskLevel

logger = logging.getLogger("titan.skills.task_continuation")

try:
    from titan.observability.metrics import metrics  # type: ignore
except Exception:
    metrics = None

class TaskContinuationSkill(BaseSkill):
    NAME = "task_continuation_skill"
    DESCRIPTION = "Detects unfinished tasks/last plans and proposes continuation/reminder actions."
    TICK_INTERVAL = 30.0  # periodic check
    SUBSCRIPTIONS = ("perception.active_window",)  # also runs on schedule
    PRIORITY = 60
    COOLDOWN = 300.0  # wait 5 minutes between continuation prompts

    MAX_LOOKBACK_SECONDS = 60 * 60 * 24 * 7  # default: 7 days

    async def on_start(self) -> None:
        await super().on_start()
        # track prompts and last continuation
        self.persistent_state.metadata.setdefault("last_continuation_ts", 0.0)
        self.persistent_state.metadata.setdefault("continuations_issued", 0)
        self.save_persistent()

    async def tick(self, ctx) -> None:
        # Periodically scan episodic store and memory for incomplete tasks
        try:
            if not self.allowed_to_act():
                return

            # If last prompt was recent, skip
            last_ts = self.persistent_state.metadata.get("last_continuation_ts", 0)
            if time.time() - last_ts < self.COOLDOWN:
                return

            # gather candidates from episodic store and memory (best-effort)
            candidates = []
            # episodic_store: look for plan/task entries with status incomplete/paused
            try:
                episodic = getattr(self.manager, "episodic_store", None) or getattr(self.manager, "app", {}).get("episodic_store", None)
                if episodic and hasattr(episodic, "query"):
                    # if query API exists, use it
                    res = episodic.query({"status": {"$in": ["paused", "incomplete", "running"]}, "ts": {"$gte": time.time() - self.MAX_LOOKBACK_SECONDS}})
                    for r in res:
                        candidates.append({"source": "episodic", "record": r})
                else:
                    # fallback: attempt to read recent 'last_episode' from context_store
                    ctx_last = ctx.get_volatile("last_episode")
                    if ctx_last:
                        candidates.append({"source": "context", "record": ctx_last})
            except Exception:
                logger.exception("TaskContinuationSkill: episodic_store scan failed")

            # memory store: look for Task objects or plan markers
            try:
                memory = getattr(self.manager, "memory", None) or getattr(self.manager, "app", {}).get("memory", None)
                if memory and hasattr(memory, "search"):
                    # light semantic check for "task" or "todo"
                    res = memory.search("incomplete task OR continue task", top_k=10)
                    for r in (res or []):
                        candidates.append({"source": "memory", "record": r})
            except Exception:
                logger.debug("Memory scan skipped or failed")

            if not candidates:
                return

            # choose top candidate heuristically (first for now)
            candidate = candidates[0]
            # craft a proposal depending on record shape
            rec = candidate.get("record", {})
            summary = rec.get("summary") or rec.get("plan_summary") or str(rec)[:400]
            intent_name = "continue_task"
            risk = RiskLevel.MEDIUM
            confidence = 0.75

            # if candidate clearly low-risk (e.g., reminder), lower risk
            if candidate.get("source") == "context":
                intent_name = "remind_about_task"
                risk = RiskLevel.LOW
                confidence = 0.6

            proposal = SkillProposal(
                skill_name=self.NAME,
                intent=intent_name,
                confidence=confidence,
                params={"candidate_source": candidate.get("source"), "summary": summary},
                risk=risk,
                timestamp=time.time(),
            )

            await ctx.publish_event({"type": "skill.proposal", "source": "skill", "proposal": proposal.model_dump() if hasattr(proposal, "model_dump") else proposal.__dict__, "skill": self.NAME, "ts": time.time()})
            # record
            self.persistent_state.metadata["last_continuation_ts"] = time.time()
            self.persistent_state.metadata["continuations_issued"] = self.persistent_state.metadata.get("continuations_issued", 0) + 1
            self.persistent_state.touch_action()
            self.save_persistent()
            self.mark_action()
            if metrics:
                metrics.counter("skill.task_continuation.proposals").inc()
        except Exception:
            logger.exception("TaskContinuationSkill.tick failed")

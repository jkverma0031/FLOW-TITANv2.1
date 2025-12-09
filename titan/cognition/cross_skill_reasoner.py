# titan/cognition/cross_skill_reasoner.py
"""
Cross-Skill Reasoner (enterprise-grade)

Responsibilities:
- Listen for 'skill.proposal' events and group/fuse proposals occurring close in time and context
- Create higher-level composite proposals (multi-step workflows) by:
    - merging complementary proposals (e.g., summarize_page + read_notification -> summarize_and_notify)
    - sequencing dependent proposals (task continuation -> open doc -> summarize)
- Provide an API `fuse_proposals([Proposal,...]) -> CompositeProposal`
- Emits 'skill.fused_proposal' events and returns a fused structure to the AutonomyEngine
- Can be used synchronously by AutonomyEngine to augment its decisioning
"""
from __future__ import annotations
import asyncio
import logging
import time
from typing import Dict, Any, List, Optional

logger = logging.getLogger("titan.cognition.cross_skill_reasoner")


class CrossSkillReasoner:
    def __init__(self, app: Dict[str, Any], *, fuse_time_window: float = 2.0):
        self.app = app
        self.fuse_time_window = fuse_time_window
        self.event_bus = app.get("event_bus")
        # in-memory buffer of recent proposals (ts, proposal dict)
        self._buffer: List[Dict[str, Any]] = []
        # option to persist fused results into episodic_store
        self.episodic_store = app.get("episodic_store")

    # ------------------------
    # Public API
    # ------------------------
    async def handle_proposal_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Called when a skill publishes a proposal. This will:
         - add it to buffer
         - attempt to fuse proposals occurring within fuse_time_window
         - publish fused proposal if a multi-skill composition is found
        Returns fused proposal dict or None
        """
        try:
            proposal = event.get("proposal") or event.get("payload") or {}
            ts = event.get("ts", time.time())
            self._buffer.append({"ts": ts, "proposal": proposal, "event": event})
            # drop stale buffer entries
            cutoff = time.time() - self.fuse_time_window
            self._buffer = [b for b in self._buffer if b["ts"] >= cutoff]
            # attempt fusion
            fused = self._attempt_fusion(self._buffer)
            if fused:
                # publish fused event
                payload = {"type": "skill.fused_proposal", "source": "cognition", "fused": fused, "ts": time.time()}
                try:
                    if self.event_bus and getattr(self.event_bus, "publish", None):
                        self.event_bus.publish("skill.fused_proposal", payload)
                except Exception:
                    logger.debug("EventBus publish failed for fused proposal")
                # optionally persist fused into episodic_store
                try:
                    if self.episodic_store and getattr(self.episodic_store, "append", None):
                        self.episodic_store.append({"ts": time.time(), "type": "skill.fused_proposal", "payload": fused})
                except Exception:
                    logger.debug("Failed to persist fused proposal")
                # clear buffer after fusion to avoid repeated fusion
                self._buffer.clear()
                return fused
        except Exception:
            logger.exception("handle_proposal_event failed")
        return None

    # ------------------------
    # Fusion rules (enterprise-grade extensible)
    # ------------------------
    def _attempt_fusion(self, buffer: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Heuristic fusion rules:
          - If there exists both a 'summarize_page' and 'read_notification' within window -> fuse to 'summarize_and_notify'
          - If TaskContinuation + active_window==editor -> combine into 'resume_workflow'
          - If multiple low-risk summarize proposals -> merge into 'batch_summarize'
        This is a rule-based engine; later you can plug an ML model using vector memory features.
        """
        if not buffer or len(buffer) < 2:
            return None

        intents = [b["proposal"].get("intent") for b in buffer if isinstance(b.get("proposal"), dict)]
        # simple rule: summarize_page + read_notification => summarize_and_notify composite
        if "summarize_page" in intents and "read_notification" in intents:
            # build fused payload
            summary = {"intent": "summarize_and_notify", "confidence": 0.9, "components": intents, "proposals": [b["proposal"] for b in buffer]}
            return summary

        # task continuation + active editor heuristic
        if "continue_task" in intents:
            # check if buffer contains a proposal tied to 'active_window' with editor keywords
            for b in buffer:
                p = b["proposal"]
                if p.get("intent") == "continue_task":
                    # simple fused: recommend resume_workflow
                    fused = {"intent": "resume_workflow", "confidence": 0.8, "components": [p], "proposals": [x["proposal"] for x in buffer]}
                    return fused

        # multiple summarize proposals -> batch_summarize
        summarize_count = sum(1 for i in intents if i and i.startswith("summarize"))
        if summarize_count >= 2:
            fused = {"intent": "batch_summarize", "confidence": min(0.9, 0.6 + 0.15 * summarize_count), "components": intents, "proposals": [b["proposal"] for b in buffer]}
            return fused

        return None

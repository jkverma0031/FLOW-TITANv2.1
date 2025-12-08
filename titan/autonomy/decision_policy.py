# titan/autonomy/decision_policy.py
from __future__ import annotations
import asyncio
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class DecisionPolicy:
    """
    Encapsulates decision logic: when to act, when to ask for confirmation,
    how to route to the Planner/Orchestrator, and policy checks.
    """

    def __init__(self, policy_engine: Optional[Any] = None, config: Optional[Any] = None):
        self.policy_engine = policy_engine
        self.config = config or {}

    async def evaluate(self, actor: str, trust_level: str, intent: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate whether to:
          - DO: proceed autonomously
          - ASK: ask the user for permission
          - IGNORE: do nothing

        Returns:
          {
            "decision": "do" | "ask" | "ignore",
            "reason": str,
            "confidence": float
          }
        """
        intent_name = intent.get("intent", "unknown")
        confidence = float(intent.get("confidence", 0.0))
        high_risk_threshold = float(getattr(self.config, "high_risk_action_threshold", 0.75))
        require_autonomy = not bool(getattr(self.config, "allow_autonomous_mode", False))

        # 1) Quick policy engine check if available
        try:
            if self.policy_engine:
                # prefer async check
                fn_async = getattr(self.policy_engine, "assess_intent_async", None)
                if fn_async and asyncio.iscoroutinefunction(fn_async):
                    allowed, meta = await fn_async(actor=actor, trust=trust_level, intent=intent, event=event)
                    if not allowed:
                        return {"decision": "ignore", "reason": "policy_denied", "confidence": 1.0}
                else:
                    # fallback to allow_action/allow_event logic
                    fn_sync = getattr(self.policy_engine, "allow_action", None)
                    if fn_sync:
                        loop = asyncio.get_event_loop()
                        allowed, meta = await loop.run_in_executor(None, lambda: fn_sync(actor, trust_level, "autonomy.intent", {"intent": intent_name, "params": intent.get("params", {})}))
                        if not allowed:
                            return {"decision": "ignore", "reason": "policy_denied", "confidence": 1.0}
        except Exception:
            logger.exception("DecisionPolicy: policy engine check failed; falling back to heuristic")

        # 2) Apply heuristics
        # If confidence is high and not require_user_confirmation -> DO
        if confidence >= high_risk_threshold and not require_autonomy:
            return {"decision": "do", "reason": "high_confidence_and_autonomy_allowed", "confidence": float(confidence)}

        # If intent is low confidence -> ASK
        if confidence < 0.35:
            return {"decision": "ask", "reason": "low_confidence", "confidence": float(confidence)}

        # If trust level is low -> ASK
        if trust_level in ("low", "untrusted"):
            return {"decision": "ask", "reason": "low_trust", "confidence": float(confidence)}

        # Default: ASK for confirmation for medium-risk intents if autonomy is not allowed
        if require_autonomy:
            return {"decision": "ask", "reason": "autonomy_not_permitted", "confidence": float(confidence)}

        # Otherwise allow DO
        return {"decision": "do", "reason": "default_allow", "confidence": float(confidence)}

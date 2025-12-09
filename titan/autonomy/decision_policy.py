# titan/autonomy/decision_policy.py
from __future__ import annotations
import logging
import asyncio
from typing import Any, Dict, Optional

from .config import AutonomyConfig

# Import the SkillProposal schema if available
try:
    from titan.autonomy.skills.proposal import SkillProposal  # type: ignore
    _HAS_PROPOSAL = True
except Exception:
    SkillProposal = None
    _HAS_PROPOSAL = False

logger = logging.getLogger("titan.autonomy.decision_policy")


class DecisionPolicy:
    """
    DecisionPolicy evaluates whether an intent or a SkillProposal should be executed,
    asked about, or ignored — taking into account a global autonomy_mode which
    may be defined in config or overridden at runtime in context_store.

    Autonomy Mode semantics:
      - 'full'       -> be permissive: allow low/medium risk actions automatically based on thresholds
      - 'hybrid'     -> default recommended mode: allow low-risk automatic actions, ask medium/high
      - 'ask_first'  -> always ask before acting (global safety switch)

    The runtime override is read from: context_store.get("autonomy_mode")
    If the context_store is not available, falls back to config.autonomy_mode.
    """

    def __init__(self, *, policy_engine: Optional[Any] = None, config: Optional[AutonomyConfig] = None, context_getter: Optional[Any] = None):
        """
        - policy_engine: optional external policy engine (legacy) your system might use
        - config: AutonomyConfig instance (contains default autonomy_mode and thresholds)
        - context_getter: callable like lambda k, default=None -> value OR any object providing .get(k, default)
        """
        self.policy_engine = policy_engine
        self.config = config or AutonomyConfig()
        # context_getter can be a function or object with .get()
        self._context_getter = context_getter
        self._default_mode = (getattr(self.config, "autonomy_mode", "hybrid") or "hybrid").lower()

        # thresholds (tunable)
        self.low_confidence_threshold = getattr(self.config, "decision_low_confidence", 0.85)
        self.medium_confidence_threshold = getattr(self.config, "decision_medium_confidence", 0.65)

    # -------------------------
    # Runtime autonomy mode helpers
    # -------------------------
    def _context_get(self, key: str, default: Optional[Any] = None) -> Any:
        try:
            if callable(self._context_getter):
                return self._context_getter(key, default)
            if self._context_getter is not None and hasattr(self._context_getter, "get"):
                return self._context_getter.get(key, default)
        except Exception:
            # swallow exceptions and fall back
            logger.debug("context_get helper failed for key=%s", key, exc_info=True)
        return default

    def get_autonomy_mode(self) -> str:
        """
        Returns active autonomy mode: one of 'full', 'hybrid', 'ask_first'.
        Context store takes precedence over config value.
        """
        try:
            val = self._context_get("autonomy_mode", None)
            if isinstance(val, str) and val.strip():
                return val.strip().lower()
        except Exception:
            logger.debug("reading autonomy_mode from context_store failed")
        return (self._default_mode or "hybrid").lower()

    def set_autonomy_mode(self, mode: str) -> None:
        """
        Convenience: if the backing context store supports .set, set the runtime mode.
        Use app/context store directly in your runtime to persist this across restarts.
        """
        try:
            if self._context_getter and hasattr(self._context_getter, "set"):
                try:
                    self._context_getter.set("autonomy_mode", mode)
                    return
                except Exception:
                    logger.exception("context_set failed in DecisionPolicy.set_autonomy_mode")
        except Exception:
            logger.exception("DecisionPolicy.set_autonomy_mode failed")
        # fallback: set into config only (non-persistent)
        try:
            self.config.autonomy_mode = mode
            self._default_mode = mode
        except Exception:
            pass

    # -------------------------
    # Public evaluate API
    # -------------------------
    async def evaluate(self, *, actor: str, trust_level: str, intent: Dict[str, Any], event: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Evaluate a normal incoming intent/event pair.
        Returns a dict like: {"decision": "do"|"ask"|"ignore", "reason": "...", "confidence": float}
        This implementation is simple and safe — you can extend rules or hook to an OPA engine.
        """
        try:
            mode = self.get_autonomy_mode()
            # if ask_first global override -> always ask
            if mode == "ask_first":
                return {"decision": "ask", "reason": "autonomy_mode_ask_first", "confidence": 0.0}

            # extract simple heuristics from intent
            conf = float(intent.get("confidence", 0.0)) if isinstance(intent, dict) else 0.0
            name = str(intent.get("intent", "") if isinstance(intent, dict) else "")

            # low-risk quick path
            if conf >= self.low_confidence_threshold:
                return {"decision": "do", "reason": "high_confidence", "confidence": conf}

            # medium confidence path
            if conf >= self.medium_confidence_threshold:
                if mode == "full":
                    return {"decision": "do", "reason": "medium_confidence_full_mode", "confidence": conf}
                # hybrid or unknown: ask instead of automatic
                return {"decision": "ask", "reason": "medium_confidence_hybrid", "confidence": conf}

            # otherwise ignore by default
            return {"decision": "ignore", "reason": "low_confidence", "confidence": conf}
        except Exception:
            logger.exception("DecisionPolicy.evaluate failed; defaulting to ignore")
            return {"decision": "ignore", "reason": "error", "confidence": 0.0}

    # -------------------------
    # Decide for SkillProposal
    # -------------------------
    async def decide_for_proposal(self, proposal: "SkillProposal") -> Dict[str, Any]:
        """
        Evaluate a SkillProposal produced by a Skill. This takes into account:
          - autonomy mode override
          - proposal.risk (low/medium/high)
          - proposal.confidence (0..1)
        Returns the same decision dict as evaluate().
        """
        try:
            mode = self.get_autonomy_mode()
            # ask_first overrides everything
            if mode == "ask_first":
                return {"decision": "ask", "reason": "autonomy_mode_ask_first", "confidence": proposal.confidence}

            # Proposal risk handling
            risk = getattr(proposal, "risk", None)
            conf = float(getattr(proposal, "confidence", 0.0) or 0.0)

            # LOW risk -> may auto-do depending on confidence + mode
            if str(risk).lower() in ("low", "low-risk", "lowrisk") or getattr(risk, "value", "").lower() == "low":
                if conf >= max(0.5, self.medium_confidence_threshold):
                    return {"decision": "do", "reason": "low_risk_confident", "confidence": conf}
                # hybrid: do only if full mode; else ask
                if mode == "full":
                    return {"decision": "do", "reason": "low_risk_full_mode", "confidence": conf}
                return {"decision": "ask", "reason": "low_risk_hybrid_ask", "confidence": conf}

            # MEDIUM risk
            if str(risk).lower() in ("medium", "medium-risk", "mediumrisk") or getattr(risk, "value", "").lower() == "medium":
                # in full mode, permit at moderately high confidence
                if mode == "full" and conf >= self.low_confidence_threshold:
                    return {"decision": "do", "reason": "medium_risk_full_confident", "confidence": conf}
                # else ask
                return {"decision": "ask", "reason": "medium_risk_default_ask", "confidence": conf}

            # HIGH risk -> always ask (safety)
            return {"decision": "ask", "reason": "high_risk_always_ask", "confidence": conf}
        except Exception:
            logger.exception("DecisionPolicy.decide_for_proposal failed; defaulting to ask")
            return {"decision": "ask", "reason": "error", "confidence": 0.0}

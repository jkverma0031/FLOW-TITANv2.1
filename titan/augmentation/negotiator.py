# Path: titan/augmentation/negotiator.py
from __future__ import annotations
from typing import Optional, Dict, List, Any
import logging

logger = logging.getLogger(__name__)


class NegotiationDecision:
    """
    Final decision returned by Negotiator.negotiate_action().
    """
    def __init__(self, provider: str, reason: str, metadata: Optional[dict] = None):
        self.provider = provider
        self.reason = reason
        self.metadata = metadata or {}

    def __repr__(self):
        return f"<NegotiationDecision provider={self.provider} reason={self.reason}>"



class Negotiator:
    """
    Enterprise-grade negotiator:
    ----------------------------------------
    - Takes an Action
    - Consults CapabilityRegistry
    - Consults PolicyEngine (if provided)
    - Scores available providers
    - Chooses best one or fails gracefully
    """

    def __init__(self, capability_registry, policy_engine=None):
        self.registry = capability_registry
        self.policy = policy_engine

    # ----------------------------------------------------------------------
    def _score_provider(self, provider_meta: dict, action: Any) -> float:
        """
        Providers can be scored based on metadata:
        - reliability
        - throughput
        - capability match
        - vendor preference
        - cost
        """
        score = 1.0

        if provider_meta.get("reliability"):
            score *= provider_meta["reliability"]

        if provider_meta.get("priority"):
            score *= provider_meta["priority"]

        # simple placeholder for extensible future scoring logic
        return score

    # ----------------------------------------------------------------------
    def negotiate_action(self, action: Any) -> Optional[NegotiationDecision]:
        """
        Main negotiation logic.
        """
        try:
            # 1. Fetch providers
            providers = self.registry.get_providers(action.type)
            if not providers:
                logger.warning(f"No providers found for action type '{action.type}'")
                return None

            # 2. Apply policy engine check if present
            if self.policy:
                decision = self.policy.check(action, identity="system")
                if not decision.allow:
                    logger.warning(f"Policy blocked action '{action}' → reason: {decision.reason}")
                    return None

            # 3. Score providers
            scored: List[tuple[str, float, dict]] = []
            for provider_name, meta in providers.items():
                s = self._score_provider(meta, action)
                scored.append((provider_name, s, meta))

            scored.sort(key=lambda x: x[1], reverse=True)

            top = scored[0]
            provider_name, provider_score, provider_meta = top

            logger.debug(f"Negotiator selected '{provider_name}' score={provider_score}")

            return NegotiationDecision(
                provider=provider_name,
                reason="Highest scoring provider",
                metadata=provider_meta
            )

        except Exception as e:
            logger.exception(f"Negotiation error: {e}")
            return None

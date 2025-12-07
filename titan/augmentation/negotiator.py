# Path: titan/augmentation/negotiator.py
from __future__ import annotations
from typing import Optional, Dict, List, Any
import logging
from titan.schemas.action import Action, ActionType

logger = logging.getLogger(__name__)


class NegotiationDecision:
    def __init__(self, provider: str, reason: str, metadata: Optional[dict] = None):
        self.provider = provider
        self.reason = reason
        self.metadata = metadata or {}

    def __repr__(self):
        return f"<NegotiationDecision provider={self.provider} reason={self.reason}>"


class Negotiator:
    """
    Enterprise-grade negotiator.
    Decides WHO runs the action (Sandbox, HostBridge, Plugin) and then EXECUTUTES it.
    """

    def __init__(self, capability_registry, policy_engine=None):
        self.registry = capability_registry
        self.policy = policy_engine

    def choose_and_execute(self, action_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        The main entry point for the Orchestrator/WorkerPool.
        1. Convert payload dict -> Action object
        2. Negotiate (decide provider)
        3. Execute via provider
        """
        try:
            # Normalize type casing
            if "type" in action_payload:
                action_payload["type"] = action_payload["type"].lower()
            
            # Construct Action model
            action = Action(**action_payload)
            
            # Negotiate
            decision = self.negotiate_action(action)
            if not decision:
                return {"success": False, "error": "Negotiation failed: Policy denied or no provider found."}

            # Fetch Provider
            provider = self.registry.get(decision.provider)
            if not provider:
                return {"success": False, "error": f"Provider '{decision.provider}' not found in registry."}

            # Execute
            # Identify interface: HostBridge uses .execute(action), Sandbox uses .run(cmd)
            if hasattr(provider, "execute"):
                # HostBridge-like interface
                return provider.execute(action)
            elif hasattr(provider, "run"):
                # Sandbox-like interface
                return provider.run(
                    action.command, 
                    timeout=action.timeout_seconds, 
                    env=action.args.get("env")
                )
            else:
                return {"success": False, "error": f"Provider '{decision.provider}' has no known execution method."}

        except Exception as e:
            logger.exception("Negotiator execution failed")
            return {"success": False, "error": str(e)}

    def negotiate_action(self, action: Action) -> Optional[NegotiationDecision]:
        try:
            # 1. Fetch providers
            providers = self.registry.list() # Simpler fallback: check all
            # In a real impl, registry.get_providers(action.type) is better
            
            # 2. Policy Check
            if self.policy:
                decision = self.policy.check(action)
                if not decision.allow:
                    logger.warning(f"Policy blocked action: {decision.reason}")
                    return None

            # 3. Simple selection logic
            if action.type == ActionType.EXEC:
                return NegotiationDecision("sandbox", "Default for EXEC")
            elif action.type == ActionType.HOST:
                return NegotiationDecision("hostbridge", "Required for HOST action")
            elif action.type == ActionType.PLUGIN:
                # Naive plugin mapping
                return NegotiationDecision(action.module, "Plugin request")
            
            return None

        except Exception as e:
            logger.exception(f"Negotiation error: {e}")
            return None
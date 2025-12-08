# titan/augmentation/negotiator.py
from __future__ import annotations
import asyncio
import logging
from typing import Optional, Dict, Any

from titan.schemas.action import Action, ActionType
from titan.runtime.plugins.registry import get_plugin

logger = logging.getLogger(__name__)

class NegotiationDecision:
    def __init__(self, provider: str, reason: str, metadata: Optional[dict] = None):
        self.provider = provider
        self.reason = reason
        self.metadata = metadata or {}

    def __repr__(self) -> str:
        return f"NegotiationDecision(provider={self.provider!r}, reason={self.reason!r}, metadata={self.metadata!r})"

class Negotiator:
    """
    Async-capable Negotiator:
      - Decides provider candidate for an Action
      - Consults a PolicyEngine (async if available)
    """

    def __init__(self, hostbridge=None, sandbox=None, policy_engine: Optional[Any] = None):
        self.hostbridge = hostbridge
        self.sandbox = sandbox
        self.policy_engine = policy_engine

    async def _policy_allow(self, actor: str, trust: str, action_name: str, resource: dict) -> tuple:
        """
        Helper: call policy_engine.allow_action in async-safe way.
        PolicyEngine may provide allow_action (sync) or allow_action_async (async).
        """
        if self.policy_engine is None:
            return True, "no_policy"
        try:
            if hasattr(self.policy_engine, "allow_action_async") and asyncio.iscoroutinefunction(self.policy_engine.allow_action_async):
                return await self.policy_engine.allow_action_async(actor, trust, action_name, resource)
            # fallback to sync function in threadpool
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: self.policy_engine.allow_action(actor, trust, action_name, resource))
        except Exception as e:
            logger.exception("PolicyEngine check failed")
            # permissive default
            return True, "policy_error_permissive"

    async def decide(self, action: Action, context: Optional[Dict[str, Any]] = None) -> Optional[NegotiationDecision]:
        try:
            ctx = context or {}
            user = ctx.get("user_id", "system")
            trust = ctx.get("trust_level", "low")

            provider = None
            reason = "default"

            if action.type == ActionType.PLUGIN:
                module_name = getattr(action, "module", None)
                if not module_name:
                    provider = "simulated"
                    reason = "missing_module"
                else:
                    plugin = get_plugin(module_name)
                    if plugin:
                        provider = module_name
                        reason = "plugin_available"
                    else:
                        provider = "sandbox"
                        reason = "plugin_missing_fallback"

            elif action.type == ActionType.HOST:
                provider = "hostbridge"
                reason = "host_required"

            elif action.type == ActionType.EXEC:
                pref = (getattr(action, "metadata", {}) or {}).get("preferred_provider")
                if pref == "hostbridge":
                    provider = "hostbridge"
                    reason = "preferred_hostbridge"
                elif pref == "plugin":
                    mod = getattr(action, "module", None)
                    if mod and get_plugin(mod):
                        provider = mod
                        reason = "preferred_plugin"
                    else:
                        provider = "sandbox"
                        reason = "preferred_plugin_missing"
                else:
                    provider = "sandbox"
                    reason = "default_exec_sandbox"

            elif action.type == ActionType.SIMULATED:
                provider = "simulated"
                reason = "simulated"

            else:
                return None

            # policy consult
            allowed, policy_reason = await self._policy_allow(user, trust, getattr(action.type, "value", str(action.type)), {"module": getattr(action, "module", None), "command": getattr(action, "command", None)})
            if not allowed:
                logger.info("Negotiator: policy denied candidate provider=%s for action=%s user=%s reason=%s", provider, action.type, user, policy_reason)
                return NegotiationDecision("denied", f"policy_denied:{policy_reason}")

            return NegotiationDecision(provider, reason)

        except Exception as e:
            logger.exception("Negotiator.decide error: %s", e)
            return None

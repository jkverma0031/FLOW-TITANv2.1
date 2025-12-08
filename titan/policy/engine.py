# titan/policy/engine.py
from __future__ import annotations
import logging
import re
from typing import Dict, Any, Tuple, Optional, List

# Optional LLM provider for "second opinion"
try:
    # assume your groq provider or other provider follows titan.models.provider.LLMProvider interface
    from titan.models.groq_provider import GroqProvider, GroqConfig  # optional, used only if configured
    _HAS_GROQ = True
except Exception:
    _HAS_GROQ = False

logger = logging.getLogger(__name__)

# Trust level ordering
_TRUST_ORDER = {"low": 0, "medium": 1, "high": 2}

# Default coarse-grained policy rules (per subsystem)
# Permissive default: allow everything unless explicitly denied.
# Each rule is a dict: {"subsystem": "<name>", "action": "<action>", "effect": "allow"|"deny"}
# When running in permissive mode, this table acts as overrides only.
DEFAULT_RULES = [
    # Example: explicitly deny risky host operations for low trust
    {"subsystem": "hostbridge", "action": "*", "effect": "deny", "min_trust": "high"},
    # Default: allow filesystem read/write for low trust (permissive)
    {"subsystem": "filesystem", "action": "*", "effect": "allow", "min_trust": "low"},
    {"subsystem": "http", "action": "*", "effect": "allow", "min_trust": "low"},
    {"subsystem": "sandbox", "action": "*", "effect": "allow", "min_trust": "medium"},
]

class PolicyEngine:
    """
    A permissive-by-default, rule-based policy engine with optional LLM 'second opinion'.
    - load_rules: accepts rule lists or path to config (not implemented file IO here)
    - allow_action: returns (bool, reason)
    """
    def __init__(self, rules: Optional[List[Dict[str, Any]]] = None, llm_provider: Optional[Any] = None, mode: str = "permissive"):
        """
        mode: "permissive" or "restrictive". We default to permissive based on your selection.
        llm_provider: optional object implementing .complete(prompt) -> str and .embed(text) -> list
        """
        self.mode = mode or "permissive"
        self.rules = rules if rules is not None else DEFAULT_RULES.copy()
        self.llm = llm_provider if llm_provider is not None else None

    def load_rules(self, rules: List[Dict[str, Any]]):
        """Replace rules list (simple API)."""
        self.rules = list(rules)

    def _match_rule(self, subsystem: str, action: str, trust_level: str) -> Optional[Dict[str, Any]]:
        """
        Find the most specific matching rule for given subsystem/action/trust.
        Rules support '*' wildcard for subsystem or action.
        'min_trust' may optionally be supplied to indicate minimum trust required for the rule to apply.
        """
        best = None
        for r in self.rules:
            subs = r.get("subsystem", "*")
            act = r.get("action", "*")
            if subs != "*" and subs != subsystem:
                continue
            # action matching: exact or wildcard or pattern
            if act != "*" and act != action:
                # support simple regex
                try:
                    if not re.fullmatch(act, action):
                        continue
                except Exception:
                    continue
            # trust check
            min_trust = r.get("min_trust")
            if min_trust:
                if _TRUST_ORDER.get(trust_level, 0) < _TRUST_ORDER.get(min_trust, 0):
                    # this rule requires higher trust than caller has -> skip
                    continue
            # We prefer first-match ordering; caller can reorder rules
            best = r
            break
        return best

    def allow_action(self, actor: str, trust_level: str, action: str, resource: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Determine whether actor (with trust_level) may perform 'action' on 'resource'.
        'action' is a coarse string, e.g., "execute_node", "plugin_call", or "filesystem.write_file".
        Resource is a dict with at least {"subsystem": "filesystem", "plugin": "filesystem", "args": {...}}.

        Returns: (allowed: bool, reason: str)
        """
        try:
            subsystem = resource.get("subsystem") or resource.get("plugin") or resource.get("module") or "unknown"
            # Compose coarse action name
            coarse_action = action
            # Attempt to match explicit rule
            rule = self._match_rule(subsystem, coarse_action, trust_level)
            if rule:
                effect = rule.get("effect", "allow")
                reason = f"matched_rule:{effect}"
                if effect == "allow":
                    return True, reason
                else:
                    return False, reason

            # If no rule matched: fallback to mode
            if self.mode == "permissive":
                # permissive default: allow
                return True, "permissive_default_allow"
            else:
                # restrictive default: deny
                return False, "restrictive_default_deny"

        except Exception as e:
            logger.exception("PolicyEngine.allow_action failed: %s", e)
            # conservative fallback: in permissive mode allow, else deny
            if self.mode == "permissive":
                return True, "policy_error_permissive_allow"
            else:
                return False, "policy_error_restrictive_deny"

    # --- Optional LLM second-opinion helper ---
    def llm_second_opinion(self, prompt: str) -> Optional[str]:
        """
        If an llm provider is present, ask it to provide a safety judgement as free text.
        Returns the raw model text or None if no provider configured.
        This is *advisory* only and not used to override explicit rules by default.
        """
        if self.llm is None:
            return None
        try:
            # We expect provider has .complete(prompt) API
            out = self.llm.complete(prompt)
            return out
        except Exception:
            logger.exception("PolicyEngine LLM second-opinion failed")
            return None

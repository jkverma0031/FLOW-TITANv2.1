# titan/policy/engine.py
from __future__ import annotations
import logging
import re
from typing import Dict, Any, Tuple, Optional, List

logger = logging.getLogger(__name__)

_TRUST_ORDER = {"low": 0, "medium": 1, "high": 2}

DEFAULT_RULES = [
    {"subsystem": "hostbridge", "action": "*", "effect": "deny", "min_trust": "high"},
    {"subsystem": "filesystem", "action": "*", "effect": "allow", "min_trust": "low"},
    {"subsystem": "http", "action": "*", "effect": "allow", "min_trust": "low"},
    {"subsystem": "sandbox", "action": "*", "effect": "allow", "min_trust": "medium"},
]

class PolicyEngine:
    """
    Simple rule engine. Mode is either 'permissive' or 'restrictive'.
    """

    def __init__(self, rules: Optional[List[Dict[str, Any]]] = None, llm_provider: Optional[Any] = None, mode: str = "permissive"):
        self.mode = (mode or "permissive").lower()
        self.rules = list(rules) if rules is not None else list(DEFAULT_RULES)
        self.llm = llm_provider

    def load_rules(self, rules: List[Dict[str, Any]]):
        self.rules = list(rules)

    def _match_rule(self, subsystem: str, action: str, trust_level: str) -> Optional[Dict[str, Any]]:
        for r in self.rules:
            subs = r.get("subsystem", "*")
            act = r.get("action", "*")
            if subs != "*" and subs != subsystem:
                continue
            if act != "*" and act != action:
                # allow regex-style action matching
                try:
                    if not re.fullmatch(act, action):
                        continue
                except Exception:
                    continue
            min_trust = r.get("min_trust")
            if min_trust:
                if _TRUST_ORDER.get(trust_level, -1) < _TRUST_ORDER.get(min_trust, -1):
                    continue
            return r
        return None

    def allow_action(self, actor: str, trust_level: str, action: str, resource: Dict[str, Any]) -> Tuple[bool, str]:
        try:
            if not trust_level:
                trust_level = "low"
            trust_level = trust_level.lower()

            subsystem = resource.get("subsystem") or resource.get("plugin") or resource.get("module") or "unknown"

            rule = self._match_rule(subsystem, action, trust_level)
            if rule:
                effect = rule.get("effect", "allow")
                reason = f"matched_rule:{effect}"
                if effect == "allow":
                    return True, reason
                else:
                    return False, reason

            if self.mode == "permissive":
                return True, "permissive_default_allow"
            else:
                return False, "restrictive_default_deny"

        except Exception as e:
            logger.exception("PolicyEngine.allow_action failed: %s", e)
            if self.mode == "permissive":
                return True, "policy_error_permissive_allow"
            else:
                return False, "policy_error_restrictive_deny"

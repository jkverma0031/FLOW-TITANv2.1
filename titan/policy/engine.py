# Path: titan/policy/engine.py
from __future__ import annotations
import logging
from typing import Any, Dict, Optional, Callable, Iterable

logger = logging.getLogger(__name__)


class PolicyDecision:
    def __init__(self, allow: bool, reason: str = "", details: Optional[dict] = None):
        self.allow = bool(allow)
        self.reason = reason or ""
        self.details = details or {}

    def to_dict(self):
        return {"allow": self.allow, "reason": self.reason, "details": self.details}


class PolicyEngine:
    """
    Enterprise policy engine. Features:
      - load in-memory rules (callables or simple dict rules)
      - pluggable external evaluation (e.g. OPA gateway) via adapter function
      - check(action, identity) -> PolicyDecision
    """

    def __init__(self, external_adapter: Optional[Callable[[dict], dict]] = None):
        """
        external_adapter receives a 'context' dict and should return a dict:
           { "allow": bool, "reason": str, "details": {...} }
        """
        self._rules: list[Callable[[dict], Optional[PolicyDecision]]] = []
        self._external_adapter = external_adapter

    # ----- Rule management -----
    def add_rule(self, fn: Callable[[dict], Optional[PolicyDecision]]) -> None:
        """
        Add a callable rule. It receives context dict and returns:
          - PolicyDecision (allow/deny) to stop evaluation, or
          - None to continue to next rule
        """
        self._rules.append(fn)

    def clear_rules(self) -> None:
        self._rules.clear()

    # ----- Evaluation -----
    def check(self, action: Any, identity: Optional[dict] = None) -> PolicyDecision:
        """
        Evaluate the action against rules and external adapter.
        The context passed to rules/adapters:
           {"action": action, "identity": identity}
        Returns PolicyDecision.
        """
        ctx = {"action": action, "identity": identity}

        # 1) internal rules (first-match)
        for rule in self._rules:
            try:
                r = rule(ctx)
                if isinstance(r, PolicyDecision):
                    return r
            except Exception:
                logger.exception("Policy rule raised exception; skipping")

        # 2) external adapter (if provided)
        if self._external_adapter:
            try:
                result = self._external_adapter(ctx)
                if isinstance(result, dict):
                    return PolicyDecision(result.get("allow", False), result.get("reason", ""), result.get("details"))
            except Exception:
                logger.exception("External policy adapter error")

        # 3) default deny
        return PolicyDecision(False, reason="no rule allowed this action", details={})

    # ----- Utilities: some common rule factories -----
    @staticmethod
    def rule_deny_if_command_contains_forbidden(forbidden: Iterable[str]):
        forbidden = [f.lower() for f in forbidden]

        def rule(ctx: dict) -> Optional[PolicyDecision]:
            action = ctx.get("action")
            # action may be pydantic model or dict
            cmd = None
            if isinstance(action, dict):
                cmd = action.get("command") or action.get("payload")
            else:
                # try attribute access
                cmd = getattr(action, "command", None) or getattr(action, "payload", None)
            if cmd is None:
                return None
            # string check
            txt = str(cmd).lower()
            for f in forbidden:
                if f in txt:
                    return PolicyDecision(False, reason=f"forbidden token matched: {f}")
            return None

        return rule

    @staticmethod
    def rule_allow_only_plugins(allowed_plugins: Iterable[str]):
        allowed = set(p.lower() for p in allowed_plugins)

        def rule(ctx: dict) -> Optional[PolicyDecision]:
            action = ctx.get("action")
            module = None
            if isinstance(action, dict):
                module = action.get("module")
            else:
                module = getattr(action, "module", None)
            if module is None:
                return None
            if str(module).lower() in allowed:
                return PolicyDecision(True, reason="allowed plugin")
            return PolicyDecision(False, reason="plugin not allowed")

        return rule

    def health(self) -> dict:
        return {"rules": len(self._rules), "external_adapter": bool(self._external_adapter)}

# titan/cognition/auto_tuner.py
from __future__ import annotations
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("titan.cognition.auto_tuner")

class AutoTuner:
    """
    Soft Auto-Tuning Engine (v2.1 safe).
    - Listens for reflection.lesson events (or receives calls via handle_action).
    - Adjusts runtime parameters stored in context_store (no code changes).
    - Rate-limited and conservative: only adjusts within safe bounds.
    - Exposes handle_action(payload) for API-driven adjustments.
    """

    # Safe parameter bounds and defaults
    SAFE_BOUNDS = {
        "planner_timeout_seconds": (2.0, 120.0),
        "orchestrator_timeout_seconds": (10.0, 600.0),
        "skill_cooldown_min": (0.0, 300.0),
        "decision_threshold_min": (0.0, 1.0),
    }

    def __init__(self, app: Dict[str, Any], *, rate_limit_seconds: int = 60):
        self.app = app
        self.context_store = app.get("context_store")
        self.session_manager = app.get("session_manager")
        self.skill_manager = app.get("skill_manager")
        self.autonomy_engine = app.get("autonomy_engine")
        self.rate_limit_seconds = rate_limit_seconds
        self._last_tune_at = 0.0

    def _now(self) -> float:
        return time.time()

    def _safe_clamp(self, key: str, value: float) -> float:
        if key not in self.SAFE_BOUNDS:
            return value
        lo, hi = self.SAFE_BOUNDS[key]
        try:
            v = float(value)
            return max(lo, min(hi, v))
        except Exception:
            return value

    def handle_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """
        Receives a dict from the reflection engine or the API.
        Example:
          {"action":"adjust_param","param":"planner_timeout_seconds","value":30}
          {"action":"adjust_skill_cooldown","skill":"notification_skill","offset":10}
        Returns a dict describing the change.
        """
        now = self._now()
        if now - self._last_tune_at < self.rate_limit_seconds:
            return {"status": "rate_limited", "next_allowed_in": int(self.rate_limit_seconds - (now - self._last_tune_at))}

        try:
            act = action.get("action")
            if act == "adjust_param":
                return self._adjust_param(action)
            if act == "adjust_skill_cooldown":
                return self._adjust_skill_cooldown(action)
            if act == "set_autonomy_mode":
                return self._set_autonomy_mode(action)
            return {"status": "unknown_action", "action": act}
        finally:
            self._last_tune_at = now

    def _adjust_param(self, action: Dict[str, Any]) -> Dict[str, Any]:
        key = action.get("param")
        val = action.get("value")
        if not key:
            return {"status": "error", "reason": "missing param"}
        val_safe = self._safe_clamp(key, val)
        # store in context_store so engine picks it up (autonomy engine reads config or context_store)
        try:
            if self.context_store and hasattr(self.context_store, "set"):
                self.context_store.set(f"tuner::{key}", val_safe)
            # also update config object in runtime (if present)
            if self.autonomy_engine and hasattr(self.autonomy_engine, "config"):
                if hasattr(self.autonomy_engine.config, key):
                    try:
                        setattr(self.autonomy_engine.config, key, val_safe)
                    except Exception:
                        pass
            return {"status": "ok", "param": key, "value": val_safe}
        except Exception:
            logger.exception("Adjust param failed")
            return {"status": "error", "reason": "persist_fail"}

    def _adjust_skill_cooldown(self, action: Dict[str, Any]) -> Dict[str, Any]:
        skill = action.get("skill")
        offset = float(action.get("offset", 0))
        if not skill:
            return {"status": "error", "reason": "missing skill"}
        # fetch skill state and adjust persistent cooldown in its state metadata (safe)
        try:
            st = None
            if self.skill_manager:
                st = self.skill_manager.get_skill_state(skill)
            if st is None:
                return {"status": "error", "reason": "skill_not_found"}
            # existing cooldown in metadata or attribute
            cur = float(st.metadata.get("cooldown", getattr(st, "cooldown", 0) or 0))
            new = max(0.0, cur + offset)
            st.metadata["cooldown"] = new
            # persist
            if self.skill_manager:
                self.skill_manager._skill_states[skill] = st
                self.skill_manager._save_persistent_state(skill, self.skill_manager.default_session_id)
            return {"status": "ok", "skill": skill, "cooldown": new}
        except Exception:
            logger.exception("adjust_skill_cooldown failed")
            return {"status": "error", "reason": "exception"}

    def _set_autonomy_mode(self, action: Dict[str, Any]) -> Dict[str, Any]:
        mode = action.get("mode")
        if mode not in ("hybrid", "ask_first", "full", None):
            return {"status": "error", "reason": "invalid_mode"}
        # set as context override (runtime)
        try:
            if self.context_store and hasattr(self.context_store, "set"):
                self.context_store.set("autonomy_mode", mode)
            # also set boolean override clear
            if mode == "ask_first":
                self.context_store.set("autonomy_ask_first", True)
            else:
                self.context_store.set("autonomy_ask_first", False)
            return {"status": "ok", "autonomy_mode": mode}
        except Exception:
            logger.exception("set_autonomy_mode failed")
            return {"status": "error", "reason": "persist_fail"}

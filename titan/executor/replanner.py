# Path: FLOW/titan/executor/replanner.py
from __future__ import annotations
from typing import Optional, Callable, Any
import logging

logger = logging.getLogger(__name__)


class Replanner:
    """
    Hook to replan when execution failures require plan modification.
    The Replanner is intentionally minimal: it calls a provided callback to request
    a new Plan when needed. The callback signature:

      replanner_fn(failure_context: Dict) -> Optional[Plan]

    If it returns a Plan, the Orchestrator will replace the plan and continue.
    """

    def __init__(self, replanner_fn: Optional[Callable[[dict], Any]] = None):
        self._fn = replanner_fn

    def maybe_replan(self, failure_context: dict):
        if not self._fn:
            return None
        try:
            return self._fn(failure_context)
        except Exception as e:
            logger.exception("Replanner callback failed: %s", e)
            return None

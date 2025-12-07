# Path: FLOW/titan/executor/retry_engine.py
from __future__ import annotations
from typing import Callable, Optional
import time
import logging
import math

logger = logging.getLogger(__name__)


class RetryEngine:
    """
    Execute a callable with retry semantics (attempts + exponential backoff).
    Returns (success: bool, result_or_exception)
    """

    def __init__(self, sleep_fn: Optional[Callable[[float], None]] = None):
        self._sleep = sleep_fn or time.sleep

    def run_with_retries(self, func: Callable[[], dict], attempts: int = 3, backoff_seconds: float = 1.0):
        attempt = 0
        last_exc = None
        while attempt < attempts:
            attempt += 1
            try:
                res = func()
                # expect result to include success boolean
                if isinstance(res, dict) and res.get("success", False):
                    return True, res
                # if runner returns non-success but not exception, treat as failure and may retry
                last_exc = res
            except Exception as e:
                last_exc = e
            # backoff
            sleep_for = backoff_seconds * (2 ** (attempt - 1))
            # jitter
            jitter = min(0.1 * sleep_for, 1.0)
            self._sleep(sleep_for + (jitter * (math.sin(attempt) if attempt else 0)))
        return False, last_exc

# Path: FLOW/titan/executor/worker_pool.py
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Any, Dict, Optional
from threading import RLock
import logging
import time

logger = logging.getLogger(__name__)


class WorkerPool:
    """
    A thin wrapper over ThreadPoolExecutor that runs task actions via a runner function.

    runner: Callable[[action_payload: Dict[str,Any]], Dict[str,Any]]
      - synchronous callable that executes one action and returns an execution result dict
    """

    def __init__(self, max_workers: int = 4, runner: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None):
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._runner = runner or self._default_runner

    def submit(self, action_payload: Dict[str, Any]) -> Future:
        """
        Submit an action payload to be executed. Returns a Future with result dict.
        """
        logger.debug("WorkerPool: submitting action %s", action_payload.get("id"))
        return self._executor.submit(self._execute_with_catch, action_payload)

    def _execute_with_catch(self, action_payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            start = time.time()
            res = self._runner(action_payload)
            res = res or {}
            res.setdefault("success", True)
            res.setdefault("duration", time.time() - start)
            return res
        except Exception as e:
            logger.exception("WorkerPool: runner failed for action %s", action_payload.get("id"))
            return {"success": False, "error": str(e)}

    def shutdown(self, wait: bool = True) -> None:
        try:
            self._executor.shutdown(wait=wait)
        except Exception:
            pass

    # Default runner: shallow no-op (should be replaced by Sandbox/HostBridge/Plugin runner)
    @staticmethod
    def _default_runner(action_payload: Dict[str, Any]) -> Dict[str, Any]:
        # Simulated execution result
        return {"success": True, "note": "default-runner-no-op"}

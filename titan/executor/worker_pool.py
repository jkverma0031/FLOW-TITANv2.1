# Path: titan/executor/worker_pool.py
from __future__ import annotations
import concurrent.futures
import threading
import time
import logging
from typing import Callable, Any, Dict, Optional
from functools import partial

logger = logging.getLogger(__name__)

class WorkerPool:
    """
    Manages a pool of threads or processes for executing actions asynchronously.
    This component ensures that TaskNodes can be executed in a controlled, 
    concurrent environment, essential for the Executor's efficiency.
    """
    
    # Default runner is a simple identity function for local testing
    @staticmethod
    def _default_runner(action_request: Dict[str, Any]) -> Dict[str, Any]:
        """A simple placeholder runner if no Negotiator is provided."""
        task_name = action_request.get('name', action_request.get('task_name', 'unknown'))
        time.sleep(0.01) # Simulate minimal work
        return {"status": "success", "message": f"Simulated execution of {task_name}", "result": {}}

    def __init__(
        self,
        max_workers: int = 8,
        runner: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        executor_type: str = 'thread' # 'thread' or 'process'
    ):
        self.max_workers = max_workers
        # The runner is the function that knows how to execute the action (usually the Negotiator)
        self.runner = runner if runner is not None else self._default_runner
        self.executor_type = executor_type.lower()
        
        # Internal state for the executor
        self._executor: Optional[concurrent.futures.Executor] = None
        self._is_running = False
        self._lock = threading.Lock()

    def start(self):
        """Initializes and starts the underlying execution pool."""
        with self._lock:
            if self._is_running:
                logger.warning("WorkerPool is already running.")
                return

            if self.executor_type == 'process':
                self._executor = concurrent.futures.ProcessPoolExecutor(max_workers=self.max_workers)
            else:
                # Default to ThreadPoolExecutor for simplicity and resource management in testing
                self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers)
            
            self._is_running = True
            logger.info(f"WorkerPool started with {self.max_workers} {self.executor_type} workers.")

    def stop(self):
        """Cleanly shuts down the execution pool."""
        with self._lock:
            if not self._is_running:
                logger.warning("WorkerPool is already stopped.")
                return

            if self._executor:
                # wait=True ensures all pending tasks complete before shutting down
                self._executor.shutdown(wait=True)
                self._executor = None
            
            self._is_running = False
            logger.info("WorkerPool stopped.")

    def submit(self, action_request: Dict[str, Any]) -> concurrent.futures.Future:
        """Submits an action request to the pool for asynchronous execution."""
        with self._lock:
            if not self._is_running or not self._executor:
                raise RuntimeError("WorkerPool must be started before submitting tasks.")
            
            # Use partial to pass action_request as the first argument to the runner
            return self._executor.submit(self.runner, action_request)

    def run_sync(self, action_request: Dict[str, Any]) -> Dict[str, Any]:
        """Executes a single action synchronously (bypassing the pool for immediate results)."""
        logger.debug(f"Executing action synchronously: {action_request.get('task_name')}")
        return self.runner(action_request)
        
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
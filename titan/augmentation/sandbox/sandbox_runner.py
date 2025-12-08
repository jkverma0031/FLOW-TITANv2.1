# titan/augmentation/sandbox/sandbox_runner.py
from __future__ import annotations
import asyncio
import subprocess
import tempfile
import os
import time
import shutil
import logging
from typing import Dict, Any, Optional

from titan.augmentation.safety import is_command_safe

logger = logging.getLogger(__name__)

class ExecutionResult(dict):
    """Mapping: success, stdout, stderr, exit_code, duration"""

class ExecutionAdapter:
    """
    Abstract adapter. If adapter implements run_command_async, it will be used.
    Otherwise blocking run_command will be executed in threadpool.
    """
    def start(self):
        return None
    def ready(self) -> bool:
        return True
    def run_command(self, command: str, timeout: int = 30, work_dir: Optional[str] = None) -> ExecutionResult:
        # fallback blocking command
        start = time.time()
        try:
            proc = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=work_dir)
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
                exit_code = proc.returncode
                stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
                stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout_text, stderr_text, exit_code = "", "timeout", -1
            return ExecutionResult({"success": exit_code == 0, "stdout": stdout_text, "stderr": stderr_text, "exit_code": exit_code, "duration": time.time() - start})
        except Exception as e:
            logger.exception("ExecutionAdapter.run_command failed")
            return ExecutionResult({"success": False, "stdout": "", "stderr": str(e), "exit_code": -1, "duration": time.time() - start})
    def cleanup(self):
        pass

class SandboxRunner:
    """
    Async-first SandboxRunner. If an adapter implements run_command_async, it's used.
    Otherwise, the blocking run_command is executed in a ThreadPoolExecutor.
    """
    def __init__(self, adapter: Optional[ExecutionAdapter] = None, work_dir: Optional[str] = None, default_timeout: int = 30, policy_engine: Optional[Any] = None):
        self.adapter = adapter
        self.work_dir = work_dir or "/tmp/titan_sandbox"
        self.default_timeout = default_timeout
        self.policy_engine = policy_engine
        os.makedirs(self.work_dir, exist_ok=True)
        if self.adapter:
            try:
                self.adapter.start()
            except Exception:
                logger.exception("SandboxRunner.adapter.start failed")

    async def _policy_check(self, command: str, context: Optional[Dict[str, Any]] = None) -> Optional[ExecutionResult]:
        if self.policy_engine is None:
            return None
        try:
            if hasattr(self.policy_engine, "allow_action_async") and asyncio.iscoroutinefunction(self.policy_engine.allow_action_async):
                allowed, reason = await self.policy_engine.allow_action_async(actor=context.get("user_id","system"), trust_level=context.get("trust_level","low"), action="sandbox.run", resource={"subsystem":"sandbox", "command":command})
            else:
                loop = asyncio.get_event_loop()
                allowed, reason = await loop.run_in_executor(None, lambda: self.policy_engine.allow_action(context.get("user_id","system"), context.get("trust_level","low"), "sandbox.run", {"subsystem":"sandbox", "command":command}))
            if not allowed:
                return ExecutionResult({"success": False, "stdout": "", "stderr": f"policy_denied:{reason}", "exit_code": -3, "duration": 0.0})
        except Exception:
            logger.exception("Policy check failed in sandbox runner; allowing by default")
        return None

    async def run_command_async(self, command: str, timeout: Optional[int] = None, context: Optional[Dict[str, Any]] = None) -> ExecutionResult:
        timeout = timeout or self.default_timeout
        context = context or {}
        # static safety
        try:
            if not is_command_safe(command):
                logger.warning("SandboxRunner: command flagged unsafe: %s", command)
                return ExecutionResult({"success": False, "stdout": "", "stderr": "command flagged unsafe", "exit_code": -2, "duration": 0.0})
        except Exception:
            logger.exception("is_command_safe failed; proceeding cautiously")

        # policy check
        denied = await self._policy_check(command, context)
        if denied is not None:
            return denied

        # delegate to adapter if async
        if self.adapter and hasattr(self.adapter, "run_command_async") and asyncio.iscoroutinefunction(self.adapter.run_command_async):
            try:
                return await self.adapter.run_command_async(command, timeout=timeout, work_dir=self.work_dir)
            except Exception:
                logger.exception("Adapter.run_command_async failed")
                return ExecutionResult({"success": False, "stdout": "", "stderr": "adapter.run_command_async error", "exit_code": -4, "duration": 0.0})

        # else run blocking run_command in threadpool
        loop = asyncio.get_event_loop()
        try:
            res = await loop.run_in_executor(None, lambda: self.adapter.run_command(command, timeout=timeout, work_dir=self.work_dir) if self.adapter else ExecutionResult({"success": False, "stdout":"", "stderr":"no adapter","exit_code":-1,"duration":0.0}))
            return res
        except Exception as e:
            logger.exception("SandboxRunner.run_command_async local run failed")
            return ExecutionResult({"success": False, "stdout": "", "stderr": str(e), "exit_code": -5, "duration": 0.0})

    def run_command(self, command: str, timeout: Optional[int] = None, context: Optional[Dict[str, Any]] = None) -> ExecutionResult:
        """
        Synchronous fallback: runs the async path or executes blocking if invoked from sync context.
        """
        # If called from running loop, submit to run_command_async thread-safely
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(self.run_command_async(command, timeout=timeout, context=context), loop)
                return fut.result()
            else:
                return asyncio.run(self.run_command_async(command, timeout=timeout, context=context))
        except Exception as e:
            logger.exception("SandboxRunner.run_command failed")
            return ExecutionResult({"success": False, "stdout": "", "stderr": str(e), "exit_code": -6, "duration": 0.0})

    def cleanup(self):
        if self.adapter:
            try:
                self.adapter.cleanup()
            except Exception:
                logger.exception("SandboxRunner.adapter.cleanup failed")

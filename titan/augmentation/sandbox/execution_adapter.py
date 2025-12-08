# titan/augmentation/sandbox/execution_adapter.py
from __future__ import annotations
import asyncio
import subprocess
import time
import logging
from typing import Optional
from titan.augmentation.sandbox.sandbox_runner import ExecutionAdapter, ExecutionResult

logger = logging.getLogger(__name__)

class LocalExecutionAdapter(ExecutionAdapter):
    """
    Async-friendly local execution adapter.
    Implements run_command_async by delegating to a thread executor.
    """

    def __init__(self, work_dir: Optional[str] = None):
        self.work_dir = work_dir

    def start(self):
        return None

    def ready(self) -> bool:
        return True

    async def run_command_async(self, command: str, timeout: int = 30, work_dir: Optional[str] = None) -> ExecutionResult:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: super().run_command(command, timeout=timeout, work_dir=work_dir or self.work_dir))

    def run_command(self, command: str, timeout: int = 30, work_dir: Optional[str] = None) -> ExecutionResult:
        return super().run_command(command, timeout=timeout, work_dir=work_dir or self.work_dir)

    def cleanup(self):
        return None

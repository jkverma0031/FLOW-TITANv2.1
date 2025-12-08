# titan/augmentation/sandbox/docker_adapter.py
from __future__ import annotations
import asyncio
import subprocess
import uuid
import logging
import time
from typing import Optional

from titan.augmentation.sandbox.sandbox_runner import ExecutionResult

logger = logging.getLogger(__name__)

MANAGED_LABEL_KEY = "managed_by"
MANAGED_LABEL_VALUE = "titan"

class DockerAdapter:
    """
    Async-friendly Docker Adapter:
    - run_command_async uses loop.run_in_executor (since docker CLI is blocking)
    - Ensures container cleanup
    """

    def __init__(self, image: str = "python:3.11-slim", work_dir: str = "/work", timeout: int = 60):
        self.image = image
        self.work_dir = work_dir
        self.timeout = timeout

    def start(self):
        # no persistent background tasks; optionally check docker availability
        try:
            subprocess.run(["docker", "info"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        except Exception:
            logger.debug("Docker CLI may be unavailable")

    def _create_container(self) -> Optional[str]:
        container_name = f"titan_{uuid.uuid4().hex[:8]}"
        cmd = [
            "docker", "run", "--rm", "-d",
            "--label", f"{MANAGED_LABEL_KEY}={MANAGED_LABEL_VALUE}",
            "-w", self.work_dir,
            "--entrypoint", "/bin/sh",
            self.image,
            "-c", "sleep 3600"
        ]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            if proc.returncode != 0:
                logger.warning("DockerAdapter: create container failed: %s", proc.stderr.decode("utf-8", errors="replace"))
                return None
            return proc.stdout.decode("utf-8", errors="replace").strip().splitlines()[0]
        except Exception:
            logger.exception("DockerAdapter._create_container failed")
            return None

    def _remove_container(self, cid: str):
        try:
            subprocess.run(["docker", "rm", "-f", cid], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        except Exception:
            logger.exception("DockerAdapter._remove_container failed")

    async def run_command_async(self, command: str, timeout: int = None, work_dir: Optional[str] = None) -> ExecutionResult:
        timeout = timeout or self.timeout
        start = time.time()
        cid = None
        loop = asyncio.get_event_loop()
        try:
            cid = await loop.run_in_executor(None, self._create_container)
            if not cid:
                return ExecutionResult({"success": False, "stdout": "", "stderr": "failed to create container", "exit_code": -1, "duration": 0.0})
            exec_cmd = ["docker", "exec", "--tty", cid, "sh", "-c", command]
            proc = await loop.run_in_executor(None, lambda: subprocess.Popen(exec_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
            try:
                stdout, stderr = await loop.run_in_executor(None, lambda: proc.communicate(timeout=timeout))
                exit_code = proc.returncode
                stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
                stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout_text, stderr_text, exit_code = "", "timeout", -1
            return ExecutionResult({"success": exit_code == 0, "stdout": stdout_text, "stderr": stderr_text, "exit_code": exit_code, "container_id": cid, "duration": time.time() - start})
        except Exception as e:
            logger.exception("DockerAdapter.run_command_async error")
            return ExecutionResult({"success": False, "stdout": "", "stderr": str(e), "exit_code": -1, "duration": time.time() - start})
        finally:
            if cid:
                try:
                    await loop.run_in_executor(None, lambda: self._remove_container(cid))
                except Exception:
                    pass

    def run_command(self, command: str, timeout: int = None):
        # sync fallback
        return asyncio.get_event_loop().run_until_complete(self.run_command_async(command, timeout=timeout))

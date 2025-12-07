# Path: titan/augmentation/sandbox/sandbox_runner.py
from __future__ import annotations
import subprocess
import tempfile
import os
import time
import shutil
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

from titan.observability.tracing import tracer
from titan.observability.metrics import metrics


class SandboxRunner:
    """
    A safe command execution sandbox.
    Provides .run() as required by TITAN tests and Orchestrator.
    """

    def __init__(self, work_dir: str = "/tmp/titan_sandbox", default_timeout: int = 30):
        self.work_dir = work_dir
        self.default_timeout = default_timeout
        os.makedirs(self.work_dir, exist_ok=True)

        # Resolve platform shell
        if shutil.which("/bin/sh"):
            self.shell = ["/bin/sh", "-c"]
        else:
            # Windows fallback
            self.shell = ["cmd.exe", "/C"]

    # ---------------------------
    # Public API required by tests
    # ---------------------------

    def run(self, command: str, timeout: Optional[int] = None,
            env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Test-facing command runner wrapper."""
        return self.run_command(command, timeout, env)

    # ---------------------------

    def run_command(self, cmd: str,
                    timeout: Optional[int] = None,
                    env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:

        timeout = timeout or self.default_timeout
        start = time.time()

        metrics.counter("sandbox.exec.calls").inc()

        with tracer.span("sandbox.exec"), metrics.timer("sandbox.exec.total"):

            with tempfile.TemporaryDirectory(prefix="titan_") as run_dir:
                try:
                    proc = subprocess.Popen(
                        self.shell + [cmd],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=run_dir,
                        env=(os.environ | (env or {})),
                    )

                    try:
                        out, err = proc.communicate(timeout=timeout)
                        success = proc.returncode == 0
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        out, err = proc.communicate()
                        success = False

                    stdout = out.decode("utf-8", errors="replace")
                    stderr = err.decode("utf-8", errors="replace")

                except Exception as e:
                    logger.exception("SandboxRunner error")
                    return {
                        "success": False,
                        "stdout": "",
                        "stderr": str(e),
                        "exit_code": -1,
                        "duration": time.time() - start,
                        "timed_out": False
                    }

        return {
            "success": success,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode,
            "duration": time.time() - start,
            "timed_out": False
        }

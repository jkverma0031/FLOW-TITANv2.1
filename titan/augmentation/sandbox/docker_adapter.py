# Path: FLOW/titan/augmentation/sandbox/docker_adapter.py
from __future__ import annotations
import subprocess
import tempfile
import os
import time
import uuid
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

MANAGED_LABEL_KEY = "managed_by"
MANAGED_LABEL_VALUE = "titan"


class DockerAdapter:
    def __init__(self, image="python:3.11-slim", work_dir="/work", timeout=60):
        self.image = image
        self.work_dir = work_dir
        self.timeout = timeout

    def available(self) -> bool:
        try:
            subprocess.check_call(["docker", "version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            return False

    def run(self, cmd: str, files: Optional[Dict[str, bytes]] = None, timeout: Optional[int] = None, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        timeout = timeout or self.timeout
        start = time.time()
        container_name = f"titan_{uuid.uuid4().hex[:8]}"

        with tempfile.TemporaryDirectory(prefix="titan_docker_") as tmpdir:
            if files:
                for name, content in files.items():
                    path = os.path.join(tmpdir, name)
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as fh:
                        fh.write(content)

            cmd_list = [
                "docker", "run", "--name", container_name,
                "--label", f"{MANAGED_LABEL_KEY}={MANAGED_LABEL_VALUE}",
                "--rm",
                "-v", f"{tmpdir}:{self.work_dir}",
                "-w", self.work_dir,
                self.image,
                "/bin/sh", "-lc", cmd
            ]

            try:
                proc = subprocess.Popen(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=(os.environ | (env or {})))
                out, err = proc.communicate(timeout=timeout)
                stdout_text = out.decode("utf-8", errors="replace")
                stderr_text = err.decode("utf-8", errors="replace")
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout_text, stderr_text, exit_code = "", "timeout", -1
            except Exception as e:
                logger.exception("DockerAdapter run error")
                stdout_text, stderr_text, exit_code = "", str(e), -1

        return {
            "success": exit_code == 0,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "exit_code": exit_code,
            "container_name": container_name,
            "duration": time.time() - start,
        }

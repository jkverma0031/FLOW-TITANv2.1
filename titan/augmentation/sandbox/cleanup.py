# Path: titan/augmentation/sandbox/cleanup.py
from __future__ import annotations
import subprocess
import logging
import shlex
from typing import List, Tuple

logger = logging.getLogger(__name__)

MANAGED_LABEL_KEY = "managed_by"
MANAGED_LABEL_VALUE = "titan"


def list_managed_containers() -> List[Tuple[str, str]]:
    try:
        cmd = (
            f'docker ps -a --filter "label={MANAGED_LABEL_KEY}={MANAGED_LABEL_VALUE}" '
            '--format "{{{{.ID}}}} {{{{.Names}}}}"'
        )
        out = subprocess.check_output(cmd, shell=True)
        lines = out.decode().strip().splitlines()
        return [tuple(line.split()) for line in lines]
    except Exception:
        return []


def remove_container(container_id: str) -> bool:
    try:
        subprocess.check_call(f"docker rm -f {shlex.quote(container_id)}", shell=True)
        return True
    except Exception as e:
        logger.warning("Failed to remove %s: %s", container_id, e)
        return False


def cleanup_orphaned_containers():
    containers = list_managed_containers()
    for cid, _ in containers:
        remove_container(cid)

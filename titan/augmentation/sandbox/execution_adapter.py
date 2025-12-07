# titan/augmentation/sandbox/execution_adapter.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

class ExecutionResult(object):
    """Standardized result object for all sandbox/hostbridge execution."""
    def __init__(self, stdout: str, stderr: str, exit_code: int, success: bool, metadata: Optional[Dict[str, Any]] = None):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.success = success
        self.metadata = metadata or {}
        
    def to_dict(self) -> Dict[str, Any]:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "success": self.success,
            "metadata": self.metadata
        }

class ExecutionAdapter(ABC):
    """
    Abstract Base Class for Sandbox/Execution environments (Docker, Firecracker, etc.).
    This decouples the SandboxRunner from the specific container technology.
    """
    
    @abstractmethod
    def initialize(self):
        """Ensure the environment (e.g., Docker daemon) is ready."""
        pass
        
    @abstractmethod
    def run_command(self, command: str, timeout: int) -> ExecutionResult:
        """Executes a single command in the isolated environment."""
        pass
        
    @abstractmethod
    def cleanup(self):
        """Stops and removes all containers/VMs managed by this adapter."""
        pass

# NOTE: The existing docker_adapter.py should be updated to inherit from ExecutionAdapter.
# For example: class DockerAdapter(ExecutionAdapter): ...
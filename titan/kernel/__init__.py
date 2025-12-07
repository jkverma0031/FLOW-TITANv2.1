# Path: titan/kernel/__init__.py
"""
TITAN Kernel Package
--------------------
The kernel is the heart of the AgentOS. It coordinates:

- Runtime (sessions, trust, context)
- Planner
- Executor
- Augmentation (sandbox, hostbridge, safety)
- Memory (vector + episodic)
- Event bus
- Diagnostics
- Lifecycle (startup + shutdown)
"""

from .kernel import Kernel
from .app_context import AppContext
from .startup import perform_kernel_startup
from .diagnostics import KernelDiagnostics
from .capability_registry import CapabilityRegistry
from .event_bus import EventBus

__all__ = [
    "Kernel",
    "AppContext",
    "CapabilityRegistry",
    "KernelDiagnostics",
    "EventBus",
    "perform_kernel_startup",
]

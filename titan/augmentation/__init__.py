# Path: FLOW/titan/augmentation/__init__.py
"""
Augmentation package (hands & senses) for TITANv2.1

Submodules:
 - sandbox: secure execution environments (local, container)
 - hostbridge: whitelisted OS capabilities via manifests
 - negotiator: choose backend for actions
 - safety: command sanitization and policy pre-checks
 - provenance: cryptographic, append-only provenance records

See structure_tree.md for layout and responsibilities. :contentReference[oaicite:1]{index=1}
"""
from .sandbox import sandbox_runner
from .sandbox import docker_adapter
from .hostbridge import hostbridge_service
from . import negotiator
from . import safety
from . import provenance
from .sandbox import cleanup


__all__ = [
    "sandbox_runner",
    "docker_adapter",
    "hostbridge_service",
    "negotiator",
    "safety",
    "provenance",
    "cleanup",
]

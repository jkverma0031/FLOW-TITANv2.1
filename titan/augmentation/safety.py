# Path: titan/augmentation/safety.py
from __future__ import annotations
from typing import Dict, Any
import re


# Very simple allow/deny rules for now
DANGEROUS_PATTERNS = [
    r"rm -rf",
    r"shutdown",
    r"format",
    r"del\s+/f",
    r":\s*(){\s*:|:\s*;}",   # fork bomb
]


def is_command_safe(command: str, metadata: Dict[str, Any] = None) -> bool:
    """
    Returns True if a sandbox command is allowed.
    Very simple pattern-based safety logic.
    """
    if not command or not isinstance(command, str):
        return False

    for pat in DANGEROUS_PATTERNS:
        if re.search(pat, command, flags=re.IGNORECASE):
            return False

    return True


def explain_unsafe(command: str) -> str:
    """Debug helper."""
    for pat in DANGEROUS_PATTERNS:
        if re.search(pat, command, flags=re.IGNORECASE):
            return f"Disallowed pattern: {pat}"
    return "Unknown reason"

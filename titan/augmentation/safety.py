# Path: titan/augmentation/safety.py
from __future__ import annotations
from typing import Dict, Any, Tuple, Optional
import re
import logging

logger = logging.getLogger(__name__)

class SafetyEngine:
    """
    Analyzes commands and actions for dangerous patterns.
    Acts as the first line of defense before the Policy Engine.
    """
    
    # Very simple allow/deny rules for now
    DANGEROUS_PATTERNS = [
        r"rm\s+-rf",           # aggressive delete
        r"shutdown",           # system shutdown
        r"mkfs",               # format disk
        r"dd\s+if=",           # raw disk write
        r":\s*(){\s*:|:\s*;}", # fork bomb
        r"wget\s+http",        # unencrypted download (example policy)
        r"curl\s+http",        # unencrypted download
        r"> /dev/sda",         # overwrite device
    ]

    def check_command(self, command: str, metadata: Dict[str, Any] = None) -> Tuple[bool, Optional[str]]:
        """
        Validates a shell command against blocklists.
        
        Returns:
            (is_safe: bool, reason: str | None)
        """
        if not command or not isinstance(command, str):
            return False, "Invalid command format"

        for pat in self.DANGEROUS_PATTERNS:
            if re.search(pat, command, flags=re.IGNORECASE):
                reason = f"Blocked by safety pattern: '{pat}'"
                logger.warning(f"SafetyEngine blocked command: {command} -> {reason}")
                return False, reason

        return True, None

# Legacy function support (optional, for older tests if needed)
def is_command_safe(command: str) -> bool:
    engine = SafetyEngine()
    safe, _ = engine.check_command(command)
    return safe
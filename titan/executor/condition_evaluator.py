# Path: titan/executor/condition_evaluator.py
from __future__ import annotations
from typing import Any, Callable, Optional
import logging

logger = logging.getLogger(__name__)

class ConditionEvaluator:
    def __init__(self, resolver: Optional[Callable[[str], Any]] = None):
        self.resolver = resolver or (lambda x: None)

    def evaluate(self, condition: str) -> Any:
        try:
            # Safe Context Helper
            class SafeContext(dict):
                def __init__(self, resolver):
                    self.resolver = resolver
                def __getitem__(self, key):
                    val = self.resolver(key)
                    if val is not None: return val
                    return MagicMockSafe(key) # Fallback for attributes

            class MagicMockSafe:
                def __init__(self, name): self.name = name
                def __getattr__(self, item): 
                    # Attempt to resolve the path "parent.child"
                    full_path = f"{self.name}.{item}"
                    # We can't access the resolver here easily without binding, 
                    # but typically the top-level resolution is enough for 'eval' 
                    # if the resolver handles dotted names.
                    return MagicMockSafe(full_path)
                def __bool__(self): return False
                def __eq__(self, other): return False
                def __gt__(self, other): return False
                def __lt__(self, other): return False

            # If the condition is a simple variable name (like "nested.val"),
            # we should try to resolve it directly first.
            direct_val = self.resolver(condition)
            if direct_val is not None:
                return direct_val

            # Otherwise, perform eval with the safe context
            # This handles "x > 5" style expressions
            safe_locals = SafeContext(self.resolver)
            return eval(condition, {"__builtins__": {}}, safe_locals)

        except Exception as e:
            logger.warning(f"Condition evaluation failed for '{condition}': {e}")
            return False
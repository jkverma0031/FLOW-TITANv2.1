# Path: titan/executor/condition_evaluator.py
from __future__ import annotations
from typing import Any, Callable, Optional, Dict, List
import logging
import ast

logger = logging.getLogger(__name__)

# Allowed node types for safe evaluation
ALLOWED_NODES = (
    ast.Expression, ast.Constant, ast.Name, ast.Load, ast.Attribute,
    ast.Compare, ast.BoolOp, ast.UnaryOp, ast.BinOp, 
    ast.And, ast.Or, ast.Not,
    ast.In, ast.NotIn, ast.Is, ast.IsNot, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)

class ConditionEvaluator:
    """
    Safe and deterministic evaluator for CFG DecisionNode conditions.
    Uses Python's AST module to prevent malicious code execution.
    """
    def __init__(self, resolver: Optional[Callable[..., Any]] = None):
        # Resolver function: (name: str, state: Optional[StateTracker]) -> Any
        # Default resolver accepts *args to be robust against 1-arg or 2-arg calls
        self.resolver = resolver or (lambda name, *args: None)

    def _safe_node_check(self, node):
        """Recursively checks if the AST node type is allowed."""
        if not isinstance(node, ALLOWED_NODES):
            raise TypeError(f"Operation not allowed: {type(node).__name__}")
        for child in ast.iter_child_nodes(node):
            self._safe_node_check(child)

    def _get_variable_context(self, condition: str) -> Dict[str, Any]:
        """
        Extracts top-level variable names (t1, t2, etc.) from the condition.
        Note: Actual value resolution happens via the resolver call in evaluate().
        """
        # This helper primarily validates structure; resolution logic is deferred to the eval() scope via the resolver
        return {} 

    def evaluate(self, condition: str) -> bool:
        """Evaluates the condition safely."""
        condition = condition.strip()
        if not condition:
            return False

        try:
            tree = ast.parse(condition, mode='eval')
            self._safe_node_check(tree)
            
            # We must prepare a context where variables trigger the resolver
            # Since we can't easily override __getitem__ in eval's globals/locals for dotted access without complex wrappers,
            # we rely on the specific resolver logic passed during Scheduler init to handle lookups.
            
            # However, for AST evaluation, we need names to resolve to values. 
            # We pre-calculate variables by inspecting the AST.
            context = {}
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    # Walk down to the base name (e.g., n1 in n1.result.code)
                    base = node
                    while isinstance(base, ast.Attribute):
                        base = base.value
                    if isinstance(base, ast.Name):
                        names.add(base.id)
            
            # Resolve all found base names using the stored resolver
            # The resolver lambda in Scheduler is bound to 'self.state', so it handles the lookup.
            for name in names:
                context[name] = self.resolver(name)

            compiled_code = compile(tree, filename='<string>', mode='eval')
            result = eval(compiled_code, {"__builtins__": {}}, context)
            
            return bool(result)

        except Exception as e:
            logger.warning(f"Condition evaluation failed for '{condition}': {type(e).__name__}: {e}")
            return False
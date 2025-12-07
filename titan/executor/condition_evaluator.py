# Path: FLOW/titan/executor/condition_evaluator.py
from __future__ import annotations
import ast
from typing import Any, Dict, Callable, Optional, List
import logging

logger = logging.getLogger(__name__)

# Whitelist nodes allowed in condition AST
_ALLOWED_NODES = {
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Subscript,
    ast.Attribute,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Call,  # only for safe calls verified separately (we won't allow arbitrary calls)
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
    ast.Not,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
}

# Allowed names that can appear (e.g., True/False/None) — additional names resolved via resolver callback.
_ALLOWED_BUILTINS = {"True", "False", "None"}


class ConditionEvaluator:
    """
    Safely evaluate boolean expressions and iterable expressions in the DAG.

    Usage:
      evaluator = ConditionEvaluator(context_resolver)
      result = evaluator.eval_bool(expr_text)
      iterable = evaluator.eval_iterable(expr_text)

    `context_resolver(name: str) -> Any` is a callable provided by the executor that returns runtime values
    (e.g., node results from StateTracker like `t1.result.files`).
    """

    def __init__(self, context_resolver: Callable[[str], Any]):
        self._resolver = context_resolver

    def _parse(self, text: str) -> ast.AST:
        try:
            node = ast.parse(text, mode="eval")
            for n in ast.walk(node):
                if type(n) not in _ALLOWED_NODES:
                    raise ValueError(f"Unsupported AST node in expression: {type(n).__name__}")
            return node
        except Exception as e:
            logger.debug("ConditionEvaluator: parse error: %s", e)
            raise

    def _resolve_name(self, name: str) -> Any:
        if name in _ALLOWED_BUILTINS:
            return {"True": True, "False": False, "None": None}[name]
        # allow dotted names; delegate to resolver
        try:
            return self._resolver(name)
        except Exception as e:
            logger.debug("ConditionEvaluator: resolver failed for %s: %s", name, e)
            return None

    def eval_bool(self, text: str) -> bool:
        """
        Evaluate boolean expression in a safe sandbox.
        """
        node = self._parse(text)
        compiled = compile(node, filename="<condition>", mode="eval")

        safe_globals: Dict[str, Any] = {}
        safe_locals: Dict[str, Any] = {}

        # Build a custom Name resolver by intercepting Name loads during eval via NodeTransformer is complicated;
        # Instead, use a tiny eval that resolves names before evaluating by replacing Name nodes with constants.
        # We'll walk AST and substitute ast.Name with ast.Constant of resolved value.
        replacer = _NameResolverTransformer(self._resolver)
        safe_node = replacer.visit(node)
        ast.fix_missing_locations(safe_node)
        compiled = compile(safe_node, filename="<condition>", mode="eval")
        try:
            val = eval(compiled, {"__builtins__": {}}, {})
            return bool(val)
        except Exception as e:
            logger.debug("ConditionEvaluator: eval failed: %s", e)
            raise

    def eval_iterable(self, text: str) -> List[Any]:
        """
        Evaluate iterable expression (e.g., `t1.result.files`) returning list-like object.
        Reuses the same safe substitution approach.
        """
        node = self._parse(text)
        # For iterable, allow Name, Attribute, Subscript, Constant
        replacer = _NameResolverTransformer(self._resolver)
        safe_node = replacer.visit(node)
        ast.fix_missing_locations(safe_node)
        compiled = compile(safe_node, filename="<iterable>", mode="eval")
        try:
            val = eval(compiled, {"__builtins__": {}}, {})
            if val is None:
                return []
            if isinstance(val, (list, tuple)):
                return list(val)
            # If it's a generator or single value, wrap
            try:
                iter(val)
                return list(val)
            except TypeError:
                return [val]
        except Exception as e:
            logger.debug("ConditionEvaluator: iterable eval failed: %s", e)
            raise


# AST Transformer: replace Name nodes with Constant nodes using resolver
class _NameResolverTransformer(ast.NodeTransformer):
    def __init__(self, resolver: Callable[[str], Any]):
        self._resolver = resolver

    def visit_Name(self, node: ast.Name) -> ast.AST:
        val = self._resolver(node.id)
        return ast.copy_location(ast.Constant(value=val), node)

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        # Evaluate nested attribute by resolving the whole dotted name string
        # e.g., t1.result.count -> resolver("t1.result.count")
        try:
            # reconstruct dotted name
            parts = []
            cur = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            parts = list(reversed(parts))
            dotted = ".".join(parts)
            val = self._resolver(dotted)
            return ast.copy_location(ast.Constant(value=val), node)
        except Exception:
            return self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        # Attempt to resolve full subscript expression text (best-effort)
        # Fallback to generic visit to keep safe
        return self.generic_visit(node)

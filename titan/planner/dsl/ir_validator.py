# Path: FLOW/titan/planner/dsl/ir_validator.py
"""
AST validator for the DSL AST produced by ir_dsl.parse_dsl.
Provides detailed diagnostics suitable to include in LLM rewrite prompts.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Set
from .ir_dsl import ASTRoot, ASTAssign, ASTTaskCall, ASTIf, ASTFor, ASTRetry, ASTExpr, ASTValue, ASTNode
import re

@dataclass
class ValidationIssue:
    kind: str  # "error" | "warning"
    message: str
    lineno: Optional[int] = None
    node: Optional[ASTNode] = None

@dataclass
class ValidationResult:
    errors: List[ValidationIssue] = field(default_factory=list)
    warnings: List[ValidationIssue] = field(default_factory=list)

    def ok(self) -> bool:
        return len(self.errors) == 0

# Helper for safe name constraint for variables produced in DSL
_VALID_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

def validate_ast(ast: ASTRoot) -> ValidationResult:
    """
    Validate AST for:
    - undefined variable references (simple conservative check)
    - invalid retry attempts/backoff
    - empty blocks
    - illegal assignments
    - reserved names used as variables (e.g. 'task', 'for', 'if', etc)
    """
    vr = ValidationResult()
    defined_vars: Set[str] = set()
    reserved = {"if", "for", "retry", "task", "else", "in"}

    def visit(node):
        if isinstance(node, ASTAssign):
            if not _VALID_VAR_RE.match(node.target):
                vr.errors.append(ValidationIssue(kind="error",
                                                 message=f"Invalid assignment target '{node.target}'. Must match { _VALID_VAR_RE.pattern }",
                                                 lineno=node.lineno, node=node))
            else:
                defined_vars.add(node.target)
            # visit RHS
            visit(node.value)
        elif isinstance(node, ASTTaskCall):
            # arguments should be simple key->value
            for k, v in node.args.items():
                # keys must be valid names
                if not _VALID_VAR_RE.match(k):
                    vr.errors.append(ValidationIssue(kind="error",
                                                     message=f"Invalid argument name '{k}' in call {node.name}()",
                                                     lineno=node.lineno, node=node))
                # check value types (ASTValue or ASTExpr)
                if isinstance(v, ASTValue):
                    pass
                elif isinstance(v, ASTExpr):
                    # may reference variables; trivially detect unknown names by scanning tokens
                    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", v.text):
                        if token not in defined_vars and token not in {"True", "False", "None"} and not token.isdigit():
                            # we *warn* but do not error because variable could be defined later in lexical order
                            vr.warnings.append(ValidationIssue(kind="warning",
                                                              message=f"Possible forward reference to '{token}' in argument of {node.name}()",
                                                              lineno=node.lineno, node=node))
                else:
                    vr.errors.append(ValidationIssue(kind="error",
                                                     message=f"Unsupported argument value type in {node.name}() for key '{k}'",
                                                     lineno=node.lineno, node=node))
        elif isinstance(node, ASTIf):
            if not node.condition or not node.condition.text.strip():
                vr.errors.append(ValidationIssue(kind="error", message="Empty if condition", lineno=node.lineno, node=node))
            if not node.body:
                vr.warnings.append(ValidationIssue(kind="warning", message="If statement has empty body", lineno=node.lineno, node=node))
            for s in node.body:
                visit(s)
            if node.orelse:
                for s in node.orelse:
                    visit(s)
        elif isinstance(node, ASTFor):
            if not _VALID_VAR_RE.match(node.iterator):
                vr.errors.append(ValidationIssue(kind="error", message=f"Invalid iterator variable '{node.iterator}'", lineno=node.lineno, node=node))
            if not node.body:
                vr.warnings.append(ValidationIssue(kind="warning", message="For loop has empty body", lineno=node.lineno, node=node))
            # check iterable expression text for obvious issues (e.g., empty)
            if not node.iterable or not node.iterable.text.strip():
                vr.errors.append(ValidationIssue(kind="error", message="For loop iterable expression is empty", lineno=node.lineno, node=node))
            for s in node.body:
                visit(s)
        elif isinstance(node, ASTRetry := ASTRetry):
            if ASTRetry.attempts < 1 or ASTRetry.attempts > 100:
                vr.errors.append(ValidationIssue(kind="error", message=f"Retry attempts must be between 1 and 100 (found {ASTRetry.attempts})", lineno=ASTRetry.lineno, node=node))
            if not ASTRetry.body:
                vr.warnings.append(ValidationIssue(kind="warning", message="Retry block has empty body", lineno=node.lineno, node=node))
            for s in ASTRetry.body:
                visit(s)
        elif isinstance(node, ASTExpr):
            # quick reserved-word checks
            if any(tok in node.text for tok in ("__import__", "eval(", "exec(")):
                vr.errors.append(ValidationIssue(kind="error", message=f"Unsafe expression detected in '{node.text}'", lineno=node.lineno, node=node))
        elif isinstance(node, ASTValue):
            pass
        elif isinstance(node, list):
            for it in node:
                visit(it)
        elif node is None:
            pass
        else:
            # Unknown node type — treat as error to be safe
            vr.errors.append(ValidationIssue(kind="error", message=f"Unsupported AST node type: {type(node)}", lineno=getattr(node, "lineno", None), node=node))

    # Visit top-level statements
    for s in ast.statements:
        visit(s)

    # Additional checks: ensure no reserved names used as variables
    for rv in reserved:
        if rv in defined_vars:
            vr.errors.append(ValidationIssue(kind="error", message=f"Reserved keyword used as variable name: '{rv}'"))

    return vr

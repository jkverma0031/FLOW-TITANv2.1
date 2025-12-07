# Path: titan/planner/dsl/ir_dsl.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Any, Dict, Union
from lark import Lark, Transformer, v_args, Token, Tree
from lark.indenter import Indenter
import os
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# Load grammar (relative file)
# ---------------------------------------------------------
GRAMMAR_PATH = os.path.join(os.path.dirname(__file__), "grammar.lark")
if not os.path.exists(GRAMMAR_PATH):
    raise FileNotFoundError(f"DSL grammar not found at {GRAMMAR_PATH}")
with open(GRAMMAR_PATH, "r", encoding="utf-8") as _f:
    GRAMMAR = _f.read()

# ---------------------------------------------------------
# Custom Indenter
# ---------------------------------------------------------
class DSLIndenter(Indenter):
    NL_type = "NEWLINE"
    INDENT_type = "INDENT"
    DEDENT_type = "DEDENT"

    OPEN_PAREN_types = ["LPAR", "_LPAR", "("]
    CLOSE_PAREN_types = ["RPAR", "_RPAR", ")"]

    tab_len = 8

# ---------------------------------------------------------
# Build parser (lalr + indenter)
# ---------------------------------------------------------
try:
    PARSER = Lark(
        GRAMMAR,
        parser="lalr",
        postlex=DSLIndenter(),
        propagate_positions=True,
        maybe_placeholders=False,
    )
except Exception as e:
    logger.exception("Failed to construct DSL parser from grammar.lark")
    raise

# ---------------------------------------------------------
# AST dataclasses
# ---------------------------------------------------------
@dataclass
class ASTNode:
    pass


@dataclass
class ASTRoot(ASTNode):
    statements: List[ASTNode] = field(default_factory=list)
    source: Optional[str] = None


@dataclass
class ASTAssign(ASTNode):
    target: str
    value: ASTNode
    lineno: Optional[int] = None


@dataclass
class ASTTaskCall(ASTNode):
    name: str  # function/task name
    args: Dict[str, Any]  # keyword args; positional args under key "_positional" if present
    lineno: Optional[int] = None


@dataclass
class ASTIf(ASTNode):
    condition: "ASTExpr"
    body: List[ASTNode]
    orelse: Optional[List[ASTNode]] = None
    lineno: Optional[int] = None


@dataclass
class ASTFor(ASTNode):
    iterator: str
    iterable: "ASTExpr"
    body: List[ASTNode]
    lineno: Optional[int] = None


@dataclass
class ASTRetry(ASTNode):
    attempts: int
    backoff: Optional[float]
    body: List[ASTNode]
    lineno: Optional[int] = None


@dataclass
class ASTExpr(ASTNode):
    text: str
    lineno: Optional[int] = None


@dataclass
class ASTValue(ASTNode):
    value: Any
    lineno: Optional[int] = None


# ---------------------------------------------------------
# Transformer: Parse-tree -> AST
# ---------------------------------------------------------
@v_args(inline=True)
class DSLTransformer(Transformer):
    def start(self, *stmts):
        if stmts and isinstance(stmts[0], list):
             return ASTRoot(statements=stmts[0])
        return ASTRoot(statements=list(stmts))

    def stmt_list(self, *stmts):
        result = []
        for stmt in stmts:
            if isinstance(stmt, list):
                result.extend(stmt)
            elif isinstance(stmt, ASTNode):
                result.append(stmt)
        return result

    def assignment(self, *items):
        if len(items) == 0:
            raise ValueError("Empty assignment")
        
        name_tok = items[0]
        expr = next((it for it in reversed(items) if isinstance(it, (ASTExpr, ASTValue, ASTTaskCall, Tree, Token))), None)
        
        target = str(name_tok) if isinstance(name_tok, Token) else str(name_tok)
        lineno = getattr(name_tok, "line", None) if isinstance(name_tok, Token) else None
        
        if isinstance(expr, Token):
            expr = self._token_to_value(expr)
        
        if expr is None:
             raise ValueError(f"Assignment RHS not found for target {target}")
             
        return ASTAssign(target=target, value=expr, lineno=lineno)

    def expr_stmt(self, expr):
        return expr

    def call_expr(self, name_tok, *maybe_args):
        name = str(name_tok)
        lineno = getattr(name_tok, "line", None) if isinstance(name_tok, Token) else None
        arg_map = {}
        positional = []
        
        if maybe_args:
            a = maybe_args[0]
            if isinstance(a, list):
                for ent in a:
                    if not isinstance(ent, (tuple, list)) or len(ent) == 0:
                        continue
                    tag = ent[0]
                    if tag == "kw" and len(ent) == 3:
                        _, key, val = ent
                        arg_map[key] = val
                    elif tag == "pos" and len(ent) == 2:
                        _, val = ent
                        positional.append(val)
                    else:
                        if isinstance(ent, ASTValue) or isinstance(ent, ASTExpr):
                            positional.append(ent)
        
        if positional:
            arg_map["_positional"] = positional
            
        return ASTTaskCall(name=name, args=arg_map, lineno=lineno)

    def arg_list(self, *args):
        return list(args)

    def keyword_arg(self, name_tok, val):
        return ("kw", str(name_tok), val)

    def positional_arg(self, val):
        return ("pos", val)

    def string(self, tok: Token):
        v = tok.value
        try:
            import ast
            v = ast.literal_eval(tok.value)
        except Exception:
            v = tok.value.strip('"').strip("'")
        return ASTValue(value=v, lineno=getattr(tok, "line", None))

    def number(self, tok: Token):
        s = tok.value
        if "." in s:
            v = float(s)
        else:
            try:
                v = int(s)
            except Exception:
                v = float(s)
        return ASTValue(value=v, lineno=getattr(tok, "line", None))

    def name(self, tok: Token):
        return ASTValue(value=str(tok), lineno=getattr(tok, "line", None))

    def attr_access(self, first_atom, *dots_and_names):
        if not dots_and_names:
            return first_atom
            
        full_text = self._serialize([first_atom] + list(dots_and_names))
        lineno = self._lineno([first_atom])
        return ASTExpr(text=full_text, lineno=lineno)

    def atom(self, *items):
        if not items:
            return None
        first = items[0]
        
        if isinstance(first, ASTNode):
            return first

        if isinstance(first, (Token, Tree)):
            if len(items) == 1 and isinstance(first, Token):
                return self._token_to_value(first)
            
            if isinstance(first, Tree) and first.data == 'expr' and len(first.children) == 1:
                return first.children[0]
            
            if isinstance(first, Tree):
                 return self.transform(first)

        return ASTValue(value=first, lineno=None)


    def expr(self, *parts):
        return ASTExpr(text=self._serialize(parts), lineno=self._lineno(parts))

    def comparison(self, *parts):
        return ASTExpr(text=self._serialize(parts), lineno=self._lineno(parts))

    def or_test(self, *parts):
        return ASTExpr(text=self._serialize(parts), lineno=self._lineno(parts))

    def and_test(self, *parts):
        return ASTExpr(text=self._serialize(parts), lineno=self._lineno(parts))

    def if_stmt(self, *items):
        cond = None
        body = []
        orelse = None
        for it in items:
            if isinstance(it, ASTExpr) and cond is None:
                cond = it
            elif isinstance(it, list) and body == []:
                body = it
            elif isinstance(it, list):
                orelse = it
        return ASTIf(condition=cond, body=body or [], orelse=orelse, lineno=(cond.lineno if cond else None))

    def for_stmt(self, *items):
        name_tok = None
        expr = None
        body = []
        for it in items:
            if isinstance(it, Token) and it.type == "NAME":
                name_tok = it
            elif isinstance(it, ASTExpr):
                if expr is None:
                    expr = it
            elif isinstance(it, list):
                body = it
        iterator = str(name_tok) if name_tok is not None else ""
        return ASTFor(iterator=iterator, iterable=expr or ASTExpr(text="", lineno=None), body=body, lineno=(name_tok.line if name_tok else None))

    def retry_stmt(self, *items):
        attempts = None
        backoff = None
        body = []
        for it in items:
            if isinstance(it, Token) and it.type == "NUMBER":
                if attempts is None:
                    try:
                        attempts = int(it.value)
                    except Exception:
                        attempts = int(float(it.value))
                elif backoff is None:
                    try:
                        backoff = float(it.value)
                    except Exception:
                        backoff = None
            elif isinstance(it, list):
                body = it
        return ASTRetry(attempts=(attempts or 1), backoff=backoff, body=body, lineno=None)

    def _token_to_value(self, tok: Token) -> ASTValue:
        if tok.type == "NUMBER":
            return self.number(tok)
        if tok.type == "ESCAPED_STRING":
            return self.string(tok)
        return ASTValue(value=str(tok), lineno=getattr(tok, "line", None))

    def _serialize(self, node) -> str:
        if node is None:
            return ""
        if isinstance(node, (list, tuple)):
            return "".join(self._serialize(x) for x in node if x is not None)
            
        if isinstance(node, ASTValue):
            return repr(node.value) if isinstance(node.value, str) else str(node.value)
        if isinstance(node, ASTExpr):
            return node.text
        if isinstance(node, Token):
            if node.type in ["EQ", "NE", "LT", "GT", "LE", "GE", "IN", "AND", "OR"]:
                 return f" {node.value} "
            if node.type == "DOT":
                return "."
            return node.value
        if isinstance(node, Tree):
            return self._serialize(node.children)
        return str(node)

    def _lineno(self, parts):
        if isinstance(parts, (list, tuple)) and parts:
            p = parts[0]
        else:
            p = parts
        if isinstance(p, Token):
            return getattr(p, "line", None)
        if isinstance(p, ASTValue):
            return p.lineno
        if isinstance(p, ASTExpr):
            return p.lineno
        return None


# ---------------------------------------------------------
# Public API
# ---------------------------------------------------------
def parse_dsl(source: str) -> ASTRoot:
    """
    Parse DSL source into ASTRoot.
    This function normalizes input (strip trailing spaces, ensure final newline)
    to help the Indenter behave consistently.
    """
    if not isinstance(source, str):
        raise TypeError("source must be a str")
    src = source.strip("\ufeff")
    src = src.rstrip()
    if not src.endswith("\n"):
        src = src + "\n"
    tree = PARSER.parse(src)
    ast = DSLTransformer().transform(tree)
    ast.source = source
    return ast
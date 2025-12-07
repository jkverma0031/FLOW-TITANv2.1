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
# Lark's Indenter requires OPEN_PAREN_types / CLOSE_PAREN_types to be defined.
# Different grammars may create tokens named "LPAR"/"RPAR" or "_LPAR"/"_RPAR".
# Provide a tolerant set so this indenter works with either style.
class DSLIndenter(Indenter):
    NL_type = "NEWLINE"
    INDENT_type = "INDENT"
    DEDENT_type = "DEDENT"

    # Accept both explicit token names and the automatic `_LPAR` variant
    OPEN_PAREN_types = ["LPAR", "_LPAR", "RPAR", "_RPAR", "("]  # "(" included defensively
    CLOSE_PAREN_types = ["RPAR", "_RPAR", "LPAR", "_LPAR", ")"]

    tab_len = 8

# ---------------------------------------------------------
# Build parser (lalr + indenter)
# ---------------------------------------------------------
# Use maybe_placeholders=False for cleaner trees; propagate_positions so we keep line numbers.
# We create the parser inside a small try/except so errors while building give a clear message.
try:
    PARSER = Lark(
        GRAMMAR,
        parser="lalr",
        postlex=DSLIndenter(),
        propagate_positions=True,
        maybe_placeholders=False,
    )
except Exception as e:
    # Re-raise with helpful context
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
# This transformer is defensive: it accepts a few shapes Lark can produce.
# ---------------------------------------------------------
@v_args(inline=True)
class DSLTransformer(Transformer):
    # Top-level
    def start(self, *stmts):
        return ASTRoot(statements=list(stmts))

    def stmt_list(self, *stmts):
        return list(stmts)

    # Assignment: grammar may include "=" as literal token or produce separate tokens.
    # We accept (NAME, "=", expr) or (NAME, expr) shapes by being permissive.
    def assignment(self, *items):
        # Items expected: NAME, maybe "=" token, expr (Tree/ASTExpr/ASTValue)
        if len(items) == 0:
            raise ValueError("Empty assignment")
        name_tok = items[0]
        # find last item that is not the '=' token
        expr = None
        for it in reversed(items[1:]):
            if isinstance(it, (Tree, Token)) or isinstance(it, (ASTExpr, ASTValue)):
                expr = it
                break
        # convert name_tok to string
        target = str(name_tok) if isinstance(name_tok, Token) else str(name_tok)
        lineno = getattr(name_tok, "line", None) if isinstance(name_tok, Token) else None
        # if expr is a Token or Tree, let other transformer methods handle it; but here we want AST node
        if isinstance(expr, Token):
            # tokens for simple name/number/string
            val = self._token_to_value(expr)
            return ASTAssign(target=target, value=val, lineno=lineno)
        # expr could be ASTValue/ASTExpr already (child transformed)
        return ASTAssign(target=target, value=expr, lineno=lineno)

    # expr_stmt -> pass-through to expr (some grammars treat separate)
    def expr_stmt(self, expr):
        return expr

    # call_expr: NAME "(" [arg_list] ")"
    # Lark might present call_expr node children as (NAME, arg_list) or (NAME, Tree) etc.
    def call_expr(self, name_tok, *maybe_args):
        name = str(name_tok)
        lineno = getattr(name_tok, "line", None) if isinstance(name_tok, Token) else None
        arg_map = {}
        positional = []
        if maybe_args:
            a = maybe_args[0]
            # arg_list transformed to list of arg entries (see arg handling below)
            if isinstance(a, list):
                for ent in a:
                    # arg entries are either ("kw", key, value) or ("pos", value)
                    if not isinstance(ent, (tuple, list)) or len(ent) == 0:
                        continue
                    tag = ent[0]
                    if tag == "kw" and len(ent) == 3:
                        _, key, val = ent
                        arg_map[key] = val
                    elif tag == "pos":
                        _, val = ent
                        positional.append(val)
                    else:
                        # fallback: if ent is ASTValue -> positional
                        if isinstance(ent, ASTValue) or isinstance(ent, ASTExpr):
                            positional.append(ent)
        if positional:
            arg_map["_positional"] = positional
        return ASTTaskCall(name=name, args=arg_map, lineno=lineno)

    # arg_list returns list of args
    def arg_list(self, *args):
        return list(args)

    # arg: NAME "=" value  -> keyword_arg
    #      | expr -> positional_arg
    def keyword_arg(self, name_tok, val):
        # transform name token to str
        return ("kw", str(name_tok), val)

    def positional_arg(self, val):
        return ("pos", val)

    # Literals
    def string(self, tok: Token):
        v = tok.value
        try:
            # try to evaluate escapes etc safely (literal_eval would be safer but using eval in a \
            # controlled way for simple quoted strings is acceptable here)
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

    # atom: may be NAME, literal, call_expr or parenthesized expr
    # The grammar may return Tree children for call_expr nested here; transformer will call corresponding methods.
    def atom(self, *items):
        if not items:
            return None
        first = items[0]

        # If first is an ASTValue already (string/number/name), return it
        if isinstance(first, ASTValue):
            return first

        # If first is a Token NAME and a call-tail is present (child after NAME),
        # some grammars can present NAME then arg_list as sibling children -> turn into a call.
        if isinstance(first, Token) and first.type == "NAME":
            name = str(first)
            lineno = getattr(first, "line", None)
            if len(items) > 1:
                # items[1] expected to be list of arg entries
                arg_list = items[1]
                # If arg_list already transformed into an ASTValue (unlikely) handle gracefully
                if isinstance(arg_list, list):
                    pos = []
                    kw = {}
                    for ent in arg_list:
                        if isinstance(ent, (tuple, list)) and ent:
                            if ent[0] == "pos":
                                pos.append(ent[1])
                            elif ent[0] == "kw":
                                _, k, v = ent
                                kw[k] = v
                            else:
                                # unknown entry -> ignore or push to positional
                                pass
                    if pos:
                        kw["_positional"] = pos
                    return ASTTaskCall(name=name, args=kw, lineno=lineno)
            # no call-tail: just a name literal
            return ASTValue(value=name, lineno=lineno)

        # If first is a Tree (like call_expr or expr) then return it (Transformer will have transformed it)
        if isinstance(first, Tree):
            # Sometimes Lark forwards nested transformed nodes as Trees; handle common contents.
            # If the tree has been transformed earlier this point may be reached rarely.
            # Fallback: return serialized text
            return ASTValue(value=str(first), lineno=None)

        # If it's an ASTExpr already
        if isinstance(first, ASTExpr):
            return first

        # Finally, if we have a bare Python value, wrap
        return ASTValue(value=first, lineno=None)

    # Expression nodes - keep textual representation so evaluators can interpret later
    def expr(self, *parts):
        return ASTExpr(text=self._serialize(parts), lineno=self._lineno(parts))

    def comparison(self, *parts):
        return ASTExpr(text=self._serialize(parts), lineno=self._lineno(parts))

    def or_test(self, *parts):
        return ASTExpr(text=self._serialize(parts), lineno=self._lineno(parts))

    def and_test(self, *parts):
        return ASTExpr(text=self._serialize(parts), lineno=self._lineno(parts))

    # If statement: items may include ASTExpr condition and lists for body/orelse
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

    # For statement: expect tokens (for NAME in expr : block)
    def for_stmt(self, *items):
        # find NAME token and expr
        name_tok = None
        expr = None
        body = []
        for it in items:
            if isinstance(it, Token) and it.type == "NAME":
                name_tok = it
            elif isinstance(it, ASTExpr):
                # first ASTExpr likely iterable expr
                if expr is None:
                    expr = it
            elif isinstance(it, list):
                body = it
        iterator = str(name_tok) if name_tok is not None else ""
        return ASTFor(iterator=iterator, iterable=expr or ASTExpr(text="", lineno=None), body=body, lineno=(name_tok.line if name_tok else None))

    # Retry statement: parse numbers for attempts/backoff and body
    def retry_stmt(self, *items):
        attempts = None
        backoff = None
        body = []
        # tokens in items may include Token(NUMBER) tokens and lists
        for it in items:
            if isinstance(it, Token) and it.type == "NUMBER":
                # first number -> attempts, second -> backoff (if present)
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

    # Helpers
    def _token_to_value(self, tok: Token) -> ASTValue:
        if tok.type == "NUMBER":
            return self.number(tok)
        if tok.type == "ESCAPED_STRING":
            return self.string(tok)
        # default to name
        return ASTValue(value=str(tok), lineno=getattr(tok, "line", None))

    def _serialize(self, node) -> str:
        # Flatten node(s) into a string resembling the original expr.
        if node is None:
            return ""
        if isinstance(node, (list, tuple)):
            return " ".join(self._serialize(x) for x in node if x is not None)
        if isinstance(node, ASTValue):
            return repr(node.value) if isinstance(node.value, str) else str(node.value)
        if isinstance(node, ASTExpr):
            return node.text
        if isinstance(node, Token):
            return node.value
        if isinstance(node, Tree):
            # join child serializations
            return " ".join(self._serialize(c) for c in node.children)
        return str(node)

    def _lineno(self, parts):
        # Return a sensible lineno from the first element where present
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
    # Normalize input: strip BOM, ensure newline at end (indenter expects newline)
    if not isinstance(source, str):
        raise TypeError("source must be a str")
    src = source.strip("\ufeff")  # remove BOM if present
    src = src.rstrip()  # remove trailing whitespace
    if not src.endswith("\n"):
        src = src + "\n"
    # Pass through parser
    tree = PARSER.parse(src)
    ast = DSLTransformer().transform(tree)
    ast.source = source
    return ast

# Path: titan/planner/dsl/ir_dsl.py
from __future__ import annotations
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from lark import Lark, Transformer, v_args, Token, Tree
from lark.indenter import Indenter
import os

# --- 1. AST Definitions ---
@dataclass
class ASTNode:
    lineno: Optional[int] = None

@dataclass
class ASTRoot(ASTNode):
    statements: List[ASTNode] = field(default_factory=list)

@dataclass
class ASTAssign(ASTNode):
    target: str = ""
    value: Any = None

@dataclass
class ASTTaskCall(ASTNode):
    name: str = ""
    args: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ASTIf(ASTNode):
    condition: ASTExpr = None
    body: List[ASTNode] = field(default_factory=list)
    orelse: List[ASTNode] = field(default_factory=list)

@dataclass
class ASTFor(ASTNode):
    iterator: str = ""
    iterable: ASTExpr = None
    body: List[ASTNode] = field(default_factory=list)

@dataclass
class ASTRetry(ASTNode):
    attempts: int = 3
    backoff: Optional[float] = None
    body: List[ASTNode] = field(default_factory=list)

@dataclass
class ASTExpr(ASTNode):
    text: str = ""

@dataclass
class ASTValue(ASTNode):
    value: Any = None

# --- 2. Indentation Handler ---
class DSLIndenter(Indenter):
    NL_type = '_NEWLINE'
    OPEN_PAREN_types = ['LPAR', 'LSQB', 'LBRACE']
    CLOSE_PAREN_types = ['RPAR', 'RSQB', 'RBRACE']
    INDENT_type = 'INDENT'
    DEDENT_type = 'DEDENT'
    tab_len = 4

# --- 3. Transformer ---
class DSLTransformer(Transformer):
    def start(self, items):
        stmts = []
        for item in items:
            if isinstance(item, ASTNode): stmts.append(item)
            elif isinstance(item, list): stmts.extend(item)
        return ASTRoot(statements=stmts)

    def simple_stmt(self, items):
        return items[0]
    
    def compound_stmt(self, items):
        return items[0]

    def assignment(self, items):
        # items: [NAME, EQ, expr] -> skip tokens
        name = items[0].value
        # Value is the last item usually
        val = items[-1]
        return ASTAssign(target=name, value=val, lineno=items[0].line)
    
    def expr_stmt(self, items):
        return items[0]

    def if_stmt(self, items):
        # items: ["if", expr, ":", suite, ("else", ":", suite)?]
        # Filter out tokens
        real_items = [x for x in items if not isinstance(x, Token)]
        
        cond = real_items[0]
        body = real_items[1]
        orelse = real_items[2] if len(real_items) > 2 else []
        
        line = getattr(cond, 'lineno', None)
        return ASTIf(condition=cond, body=body, orelse=orelse, lineno=line)

    def for_stmt(self, items):
        # items: ["for", NAME, "in", expr, ":", suite]
        # Robust filtering:
        # 1. Iterator name (Token)
        # 2. Iterable (ASTExpr)
        # 3. Body (List)
        
        iterator = None
        iterable = None
        body = []
        
        for item in items:
            if isinstance(item, Token) and item.type == 'NAME' and not iterator:
                iterator = item.value
            elif isinstance(item, ASTExpr) and not iterable:
                iterable = item
            elif isinstance(item, list): # The suite
                body = item
        
        # Line number from first token
        line = items[0].line if isinstance(items[0], Token) else None
        
        return ASTFor(iterator=iterator, iterable=iterable, body=body, lineno=line)

    def retry_stmt(self, items):
        attempts = 3
        backoff = 1.0
        body = items[-1] # Body is always last
        
        for i, item in enumerate(items):
            if isinstance(item, Token) and item.type == 'NUMBER':
                # First number is attempts, second is backoff
                if "attempts" in str(items[i-2]): attempts = int(item.value)
                if "backoff" in str(items[i-2]): backoff = float(item.value)
                
        return ASTRetry(attempts=attempts, backoff=backoff, body=body)

    def suite(self, items):
        # Filter tokens (INDENT/DEDENT/NEWLINE)
        return [x for x in items if isinstance(x, (ASTNode, list))] 

    # Expression Reconstructors
    def _reconstruct(self, items):
        if len(items) == 1 and isinstance(items[0], ASTTaskCall):
            return items[0]

        parts = []
        line = None
        for it in items:
            if isinstance(it, ASTExpr): 
                parts.append(it.text)
                if not line: line = it.lineno
            elif isinstance(it, ASTTaskCall):
                parts.append(it.name) # Fallback if inside complex expr
                if not line: line = it.lineno
            elif isinstance(it, ASTValue):
                parts.append(str(it.value))
                if not line: line = it.lineno
            elif isinstance(it, Token):
                parts.append(it.value)
                if not line: line = it.line
            else:
                parts.append(str(it))
        return ASTExpr(text=" ".join(parts).replace(" . ", "."), lineno=line)

    def or_test(self, items): return self._reconstruct(items)
    def and_test(self, items): return self._reconstruct(items)
    def comparison(self, items): return self._reconstruct(items)
    def attr_access(self, items): 
        # Special handling to join dots without spaces
        if len(items) == 1 and isinstance(items[0], ASTTaskCall): return items[0]
        parts = []
        line = None
        for it in items:
            if isinstance(it, Token):
                parts.append(it.value)
                if not line: line = it.line
            elif isinstance(it, ASTExpr):
                parts.append(it.text)
                if not line: line = it.lineno
            elif isinstance(it, ASTTaskCall):
                parts.append(it.name)
        return ASTExpr(text="".join(parts), lineno=line)

    def expr(self, items): return items[0]

    def call_expr(self, items):
        name = items[0].value
        # args is the second item if present (index 1), skipping LPAR
        # items: [NAME, LPAR, arg_list, RPAR] -> filter tokens
        args = {}
        for it in items:
            if isinstance(it, dict): args = it
            
        return ASTTaskCall(name=name, args=args, lineno=items[0].line)

    def arg_list(self, items):
        args = {}
        for item in items:
            if isinstance(item, dict): args.update(item)
        return args

    def keyword_arg(self, items):
        # items: [NAME, EQ, expr]
        return {items[0].value: items[2]}

    def positional_arg(self, items):
        return {}

    def atom(self, items):
        item = items[0]
        if isinstance(item, ASTTaskCall): return item
        if isinstance(item, Token): return ASTExpr(text=item.value, lineno=item.line)
        return item

    def value(self, items):
        item = items[0]
        if item.type == 'ESCAPED_STRING': return ASTValue(value=item.value[1:-1], lineno=item.line)
        elif item.type == 'NUMBER':
            try: return ASTValue(value=int(item.value))
            except: return ASTValue(value=float(item.value))
        elif item.type == 'NAME': return ASTExpr(text=item.value, lineno=item.line)
        return ASTValue(value=item.value, lineno=item.line)

# --- 4. Parser Init ---
_grammar_path = os.path.join(os.path.dirname(__file__), "grammar.lark")
with open(_grammar_path, "r") as f:
    _grammar = f.read()

PARSER = Lark(_grammar, parser="lalr", lexer="contextual", postlex=DSLIndenter(), start="start")

def parse_dsl(code: str) -> ASTRoot:
    tree = PARSER.parse(code + "\n")
    return DSLTransformer().transform(tree)
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
    condition: Any = None
    body: List[ASTNode] = field(default_factory=list)
    orelse: List[ASTNode] = field(default_factory=list)

@dataclass
class ASTFor(ASTNode):
    iterator: str = ""
    iterable: Any = None
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

    @v_args(inline=True)
    def assignment(self, name, _, val):
        # name is Token, val is ASTExpr or ASTTaskCall
        return ASTAssign(target=name.value, value=val, lineno=name.line)

    def expr_stmt(self, items):
        return items[0]

    def if_stmt(self, items):
        # items: ["if", expr, ":", suite, ("else", ":", suite)?]
        
        # Filtering to find condition (ASTExpr/ASTTaskCall), first body (List), second body (List)
        real_items = [x for x in items if not isinstance(x, Token)]
        
        cond = real_items[0]
        body = real_items[1]
        orelse = real_items[2] if len(real_items) > 2 else []
        
        line = getattr(cond, 'lineno', None)
        return ASTIf(condition=cond, body=body, orelse=orelse, lineno=line)

    def for_stmt(self, items):
        # items: ["for", NAME, "in", expr, ":", suite]
        
        # Find NAME (iterator), ASTExpr/ASTTaskCall (iterable), Body (List)
        iterator = next(item.value for item in items if isinstance(item, Token) and item.type == 'NAME')
        iterable = next(item for item in items if isinstance(item, (ASTExpr, ASTTaskCall, ASTValue)))
        body = next(item for item in items if isinstance(item, list))
        
        # Line number from first token
        line = items[0].line if isinstance(items[0], Token) else None
        
        return ASTFor(iterator=iterator, iterable=iterable, body=body, lineno=line)

    def retry_stmt(self, items):
        # items: ["retry", "attempts", EQ, NUMBER, ("backoff", EQ, NUMBER)?, ":", suite]
        
        attempts = 3
        backoff = 1.0
        
        # Find all numbers
        numbers = [float(item.value) for item in items if isinstance(item, Token) and item.type == 'NUMBER']
        
        # By grammar definition: first number is attempts, second (if present) is backoff
        if len(numbers) >= 1: attempts = int(numbers[0])
        if len(numbers) >= 2: backoff = numbers[1]
        
        body = items[-1] # Body is always last
                
        return ASTRetry(attempts=attempts, backoff=backoff, body=body, lineno=items[0].line)

    def suite(self, items):
        # Filter tokens (INDENT/DEDENT/NEWLINE)
        result = []
        for x in items:
            if isinstance(x, (ASTNode, list)): 
                result.append(x)
        return result

    # Expression Reconstructors - Simplified for robustness
    @v_args(inline=True)
    def _reconstruct(self, *items):
        # Combine all parts into a single string for ASTExpr.text
        parts = []
        line = None
        for item in items:
            if isinstance(item, Token):
                parts.append(item.value)
                if not line: line = item.line
            elif isinstance(item, ASTExpr):
                parts.append(item.text)
                if not line: line = item.lineno
            elif isinstance(item, (ASTTaskCall, ASTValue)):
                # Handle nested TaskCall/ASTValue in expressions by taking their text/value
                val_text = getattr(item, 'text', str(getattr(item, 'value', '')))
                parts.append(val_text)
                if not line: line = getattr(item, 'lineno', line)

        # Connect dot notation and normalize spacing for robust evaluation
        text = " ".join(parts)
        text = text.replace(" . ", ".").replace(" .", ".").replace(". ", ".")
        text = text.replace(" == ", "==").replace(" != ", "!=").strip()
        
        # Attempt to get a line number
        return ASTExpr(text=text, lineno=line)

    def or_test(self, items): return self._reconstruct(*items)
    def and_test(self, items): return self._reconstruct(*items)
    def comparison(self, items): return self._reconstruct(*items)
    
    @v_args(inline=True)
    def attr_access(self, *items):
        return self._reconstruct(*items)

    def expr(self, items): return items[0]

    def call_expr(self, items):
        name = items[0].value
        # args is a dictionary (from arg_list rule)
        args = next((item for item in items if isinstance(item, dict)), {})
        return ASTTaskCall(name=name, args=args, lineno=items[0].line)

    def arg_list(self, items):
        args = {}
        for item in items:
            if isinstance(item, dict): args.update(item)
        return args

    @v_args(inline=True)
    def keyword_arg(self, name, _, val):
        # name is Token, val is ASTExpr or ASTValue
        return {name.value: val}

    def positional_arg(self, items):
        return {}

    def atom(self, items):
        item = items[0]
        if isinstance(item, ASTTaskCall): return item
        if isinstance(item, ASTValue): return item
        if isinstance(item, ASTExpr): return item # For paren-wrapped expressions
        return item 

    # NEW RULES from grammar.lark (Value definitions)
    def string_value(self, items):
        item = items[0]
        return ASTValue(value=item.value[1:-1], lineno=item.line)
        
    def number_value(self, items):
        item = items[0]
        try: return ASTValue(value=int(item.value), lineno=item.line)
        except ValueError: return ASTValue(value=float(item.value), lineno=item.line)
        
    def name_value(self, items):
        item = items[0]
        # Represents a variable name being used as a literal value in an argument
        return ASTExpr(text=item.value, lineno=item.line)


# --- 4. Parser Init ---
_grammar_path = os.path.join(os.path.dirname(__file__), "grammar.lark")
with open(_grammar_path, "r") as f:
    _grammar = f.read()

PARSER = Lark(_grammar, parser="lalr", lexer="contextual", postlex=DSLIndenter(), start="start")

def parse_dsl(code: str) -> ASTRoot:
    # Lark requires an extra newline at the end of the source string for the indenter to output a DEDENT token at EOF.
    tree = PARSER.parse(code + "\n")
    return DSLTransformer().transform(tree)
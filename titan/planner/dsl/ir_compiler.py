# Path: titan/planner/dsl/ir_compiler.py
from __future__ import annotations
from typing import List, Dict, Optional, Tuple, Any, Set
from dataclasses import dataclass, field
from uuid import uuid4
import itertools
import logging

from titan.schemas.graph import CFG, TaskNode, DecisionNode, LoopNode, RetryNode, NoOpNode, StartNode, EndNode, NodeBase, NodeType, Edge
from .ir_dsl import ASTRoot, ASTAssign, ASTTaskCall, ASTIf, ASTFor, ASTRetry, ASTExpr, ASTValue, ASTNode

logger = logging.getLogger(__name__)

def _node_id(prefix: str, counter: itertools.count = itertools.count()):
    return f"{prefix}_{next(counter):06d}"

@dataclass
class CompileContext:
    cfg: CFG = field(default_factory=CFG)
    var_node_map: Dict[str, str] = field(default_factory=dict)
    last_node_id: Optional[str] = None
    counter: itertools.count = field(default_factory=itertools.count)


def compile_ast_to_cfg(ast: Any) -> CFG:
    """
    Compile the AST into a CFG.
    Handles ASTRoot (from DSL parser) or dict/list (from heuristic parser).
    """
    cfg = CFG()
    
    # 1. Normalize input into a list of statements/nodes
    statements = []
    
    # Case A: ASTRoot from Lark parser
    if hasattr(ast, "statements"):
        statements = ast.statements
    # Case B: Heuristic parser dict
    elif hasattr(ast, "nodes"):
        statements = getattr(ast, "nodes")
    elif isinstance(ast, dict) and "nodes" in ast:
        statements = ast["nodes"]
    elif isinstance(ast, list):
        statements = ast
    else:
        statements = []

    # 2. Compile
    # If the input is already a list of Node objects (heuristic), we add them directly.
    # If it is DSL AST nodes, we compile them recursively.
    
    # Check if this is a pre-built node list (heuristic parser output) or DSL AST
    is_dsl_ast = len(statements) > 0 and isinstance(statements[0], ASTNode)

    if not is_dsl_ast:
        # --- PATH A: Pre-defined nodes (Heuristic) ---
        node_list = []
        for an in statements:
            # (Same logic as before for raw dictionaries)
            nid = an.get("id")
            ntype = an.get("type")
            meta = an.get("metadata", {}) or {}
            name = an.get("name", None)
            
            # Map types to Node classes
            if ntype == "start": node = StartNode(id=nid, name=name, metadata=meta)
            elif ntype == "end": node = EndNode(id=nid, name=name, metadata=meta)
            elif ntype == "task":
                node = TaskNode(
                    id=nid, name=name, metadata=meta,
                    task_ref=an.get("task_ref", nid),
                    timeout_seconds=an.get("timeout_seconds"),
                    supports_parallel=an.get("supports_parallel", False)
                )
            elif ntype == "decision":
                node = DecisionNode(id=nid, name=name, metadata=meta, condition=an.get("condition", "False"))
            elif ntype == "loop":
                node = LoopNode(
                    id=nid, name=name, metadata=meta,
                    iterator_var=an.get("iterator_var", f"it_{nid}"),
                    iterable_expr=an.get("iterable_expr", ""),
                    max_iterations=an.get("max_iterations", 1000),
                    continue_on_error=an.get("continue_on_error", False)
                )
            elif ntype == "retry":
                node = RetryNode(
                    id=nid, name=name, metadata=meta,
                    attempts=an.get("attempts", 3),
                    backoff_seconds=an.get("backoff_seconds", 1.0),
                    child_node_id=an.get("child_node_id")
                )
            else:
                node = NoOpNode(id=nid, name=name, metadata=meta)

            cfg.add_node(node)
            node_list.append(nid)

        # Wire sequentially for heuristic nodes
        prev = None
        for nid in node_list:
            if prev:
                cfg.add_edge(prev, nid, label="next")
            prev = nid
            
        if node_list:
            cfg.entry = node_list[0]
            cfg.exit = node_list[-1]

    else:
        # --- PATH B: DSL AST Compilation (Compiler) ---
        ctx = CompileContext(cfg=cfg)
        
        # Add implicit Start node
        start_id = _node_id("start", ctx.counter)
        cfg.add_node(StartNode(id=start_id, name="start"))
        cfg.entry = start_id
        
        prev_id = start_id
        
        for stmt in statements:
            prev_id = _compile_stmt(stmt, ctx, prev_id)
            
        # Add implicit End node
        end_id = _node_id("end", ctx.counter)
        cfg.add_node(EndNode(id=end_id, name="end"))
        cfg.exit = end_id
        cfg.add_edge(prev_id, end_id, label="next")

    return cfg


def _compile_stmt(stmt: ASTNode, ctx: CompileContext, prev_id: str) -> str:
    """Compile a single statement, return last node ID."""
    if isinstance(stmt, ASTAssign):
        if isinstance(stmt.value, ASTTaskCall):
            node_id = _create_tasknode_from_call(stmt.value, ctx, assign_var=stmt.target)
            ctx.cfg.add_edge(prev_id, node_id, label="next")
            return node_id
        else:
            raise ValueError(f"Unsupported assignment RHS at line {stmt.lineno}")
    elif isinstance(stmt, ASTTaskCall):
        node_id = _create_tasknode_from_call(stmt, ctx, assign_var=None)
        ctx.cfg.add_edge(prev_id, node_id, label="next")
        return node_id
    elif isinstance(stmt, ASTIf):
        return _compile_if(stmt, ctx, prev_id)
    elif isinstance(stmt, ASTFor):
        return _compile_for(stmt, ctx, prev_id)
    elif isinstance(stmt, ASTRetry):
        return _compile_retry(stmt, ctx, prev_id)
    else:
        # Fallback for unknown statements (e.g. comments or expressions) -> NoOp
        noop_id = _node_id("noop", ctx.counter)
        ctx.cfg.add_node(NoOpNode(id=noop_id, name="noop_stmt"))
        ctx.cfg.add_edge(prev_id, noop_id, label="next")
        return noop_id


def _create_tasknode_from_call(call: ASTTaskCall, ctx: CompileContext, assign_var: Optional[str]) -> str:
    if assign_var:
        task_ref = assign_var
    else:
        task_ref = f"task_{uuid4().hex[:8]}"

    nid = _node_id("task", ctx.counter)
    # Serialize args
    safe_args = _serialize_call_args(call.args)
    
    tn = TaskNode(
        id=nid, 
        name=f"task:{call.name}", 
        task_ref=task_ref, 
        metadata={"dsl_call": {"name": call.name, "args": safe_args}}
    )
    ctx.cfg.add_node(tn)
    
    if assign_var:
        ctx.var_node_map[assign_var] = nid
    return nid


def _compile_if(node: ASTIf, ctx: CompileContext, prev_id: str) -> str:
    nid = _node_id("dec", ctx.counter)
    dn = DecisionNode(id=nid, name="decision", condition=node.condition.text, metadata={"source_lineno": node.lineno})
    ctx.cfg.add_node(dn)
    ctx.cfg.add_edge(prev_id, nid, label="next")

    # True Branch
    body_prev = nid
    body_entry = None
    if node.body:
        for stmt in node.body:
            body_prev = _compile_stmt(stmt, ctx, body_prev)
            if body_entry is None:
                # Find the node we just added (successor of nid)
                succs = ctx.cfg.get_successors(nid)
                if succs: body_entry = succs[0]
    
    # If body empty or failed to wire, force NoOp
    if body_entry is None:
        noop = NoOpNode(id=_node_id("noop", ctx.counter), name="noop_true")
        ctx.cfg.add_node(noop)
        ctx.cfg.add_edge(nid, noop.id, label="true")
        body_prev = noop.id
    else:
        # Ensure the edge out of decision is labeled 'true'
        # (It was added as 'next' inside _compile_stmt's implicit wiring if we aren't careful, 
        # so we rely on _compile_stmt connecting to body_prev which WAS nid.
        # Actually _compile_stmt adds edge (prev, node). 
        # So we need to relabel that edge.)
        for i, e in enumerate(ctx.cfg.edges):
            if e.source == nid and e.target == body_entry:
                ctx.cfg.edges[i].label = "true"
                break

    # False Branch
    false_prev = nid
    false_entry = None
    if node.orelse:
        for stmt in node.orelse:
            false_prev = _compile_stmt(stmt, ctx, false_prev)
            if false_entry is None:
                succs = [e.target for e in ctx.cfg.edges if e.source == nid and e.label == "next"] # temp label
                if succs: false_entry = succs[0]
        
        # Relabel
        if false_entry:
            for i, e in enumerate(ctx.cfg.edges):
                if e.source == nid and e.target == false_entry:
                    ctx.cfg.edges[i].label = "false"
                    break
    
    if false_entry is None:
        # Explicit NoOp for false path
        noop_f = NoOpNode(id=_node_id("noop", ctx.counter), name="noop_false")
        ctx.cfg.add_node(noop_f)
        ctx.cfg.add_edge(nid, noop_f.id, label="false")
        false_prev = noop_f.id

    # Join
    join = NoOpNode(id=_node_id("noop", ctx.counter), name="join")
    ctx.cfg.add_node(join)
    ctx.cfg.add_edge(body_prev, join.id, label="next")
    ctx.cfg.add_edge(false_prev, join.id, label="next")
    
    return join.id


def _compile_for(node: ASTFor, ctx: CompileContext, prev_id: str) -> str:
    loop_id = _node_id("loop", ctx.counter)
    ln = LoopNode(id=loop_id, name="loop", iterator_var=node.iterator, iterable_expr=node.iterable.text)
    ctx.cfg.add_node(ln)
    ctx.cfg.add_edge(prev_id, loop_id, label="next")

    body_prev = loop_id
    body_entry = None
    
    for stmt in node.body:
        body_prev = _compile_stmt(stmt, ctx, body_prev)
        if body_entry is None:
            succs = [e.target for e in ctx.cfg.edges if e.source == loop_id]
            if succs: body_entry = succs[0]

    if body_entry:
        # Label loop->body
        for i, e in enumerate(ctx.cfg.edges):
            if e.source == loop_id and e.target == body_entry:
                ctx.cfg.edges[i].label = "body"
                break
    else:
        noop = NoOpNode(id=_node_id("noop", ctx.counter), name="noop_body")
        ctx.cfg.add_node(noop)
        ctx.cfg.add_edge(loop_id, noop.id, label="body")
        body_prev = noop.id

    # Back edge
    ctx.cfg.add_edge(body_prev, loop_id, label="continue")

    # Exit node
    exit_node = NoOpNode(id=_node_id("noop", ctx.counter), name="loop_exit")
    ctx.cfg.add_node(exit_node)
    ctx.cfg.add_edge(loop_id, exit_node.id, label="break")
    
    return exit_node.id


def _compile_retry(node: ASTRetry, ctx: CompileContext, prev_id: str) -> str:
    retry_id = _node_id("retry", ctx.counter)
    rn = RetryNode(
        id=retry_id, name="retry", 
        attempts=node.attempts, 
        backoff_seconds=node.backoff if node.backoff else 1.0
    )
    ctx.cfg.add_node(rn)
    ctx.cfg.add_edge(prev_id, retry_id, label="next")

    body_prev = retry_id
    child_id = None
    
    for stmt in node.body:
        body_prev = _compile_stmt(stmt, ctx, body_prev)
        if child_id is None:
            succs = [e.target for e in ctx.cfg.edges if e.source == retry_id]
            if succs: child_id = succs[0]

    if child_id:
        rn.child_node_id = child_id
    else:
        # Empty retry block
        noop = NoOpNode(id=_node_id("noop", ctx.counter), name="noop_retry")
        ctx.cfg.add_node(noop)
        ctx.cfg.add_edge(retry_id, noop.id, label="next") # Retry engine expects implicit flow if logic manually handled
        rn.child_node_id = noop.id
        body_prev = noop.id

    success_node = NoOpNode(id=_node_id("noop", ctx.counter), name="retry_success")
    ctx.cfg.add_node(success_node)
    ctx.cfg.add_edge(body_prev, success_node.id, label="next")
    
    return success_node.id


def _serialize_call_args(args: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in args.items():
        if isinstance(v, ASTValue):
            out[k] = v.value
        elif isinstance(v, ASTExpr):
            out[k] = {"expr": v.text}
        else:
            out[k] = v
    return out
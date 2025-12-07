# Path: FLOW/titan/planner/dsl/ir_compiler.py
"""
AST -> CFG compiler.

Produces a titan.schemas.graph.CFG instance. The compiler intentionally:
- generates deterministic node ids (based on a monotonically increasing counter + readable prefixes),
- creates Node objects from your titan.schemas.graph module,
- wires edges deterministically,
- fills TaskNode.metadata with DSL references so the Planner can instantiate real Tasks later.
"""

from __future__ import annotations
from typing import List, Dict, Optional, Tuple, Any, Set
from dataclasses import dataclass, field
from uuid import uuid4
import itertools
import logging

from titan.schemas.graph import CFG, TaskNode, DecisionNode, LoopNode, RetryNode, NoOpNode, StartNode, EndNode, NodeBase, NodeType, Edge

from .ir_dsl import ASTRoot, ASTAssign, ASTTaskCall, ASTIf, ASTFor, ASTRetry, ASTExpr, ASTValue, ASTNode

logger = logging.getLogger(__name__)

# ID generator with deterministic sequence per compiler instance
def _node_id(prefix: str, counter: itertools.count = itertools.count()):
    return f"{prefix}_{next(counter):06d}"

@dataclass
class CompileContext:
    cfg: CFG = field(default_factory=CFG)
    var_node_map: Dict[str, str] = field(default_factory=dict)  # maps assignment var -> node id (task nodes)
    last_node_id: Optional[str] = None
    counter: itertools.count = field(default_factory=itertools.count)


def compile_ast_to_cfg(ast: Any) -> CFG:
    """
    Compile the AST into a CFG. We build nodes, then analyze variable/data dependencies.
    If producers/consumers info is present, we add edges producer->consumer.
    Otherwise we fall back to original sequential ordering.
    """
    cfg = CFG()
    node_list = []

    # AST may be either object-like or dict-like; we support both shapes.
    raw_nodes = []
    if hasattr(ast, "nodes"):
        raw_nodes = getattr(ast, "nodes")
    elif isinstance(ast, dict) and "nodes" in ast:
        raw_nodes = ast["nodes"]
    elif isinstance(ast, list):
        raw_nodes = ast
    else:
        # Single-node AST?
        raw_nodes = []

    for an in raw_nodes:
        nid = an.get("id")
        ntype = an.get("type")
        meta = an.get("metadata", {}) or {}
        name = an.get("name", None)
        if ntype == "start":
            node = StartNode(id=nid, name=name, metadata=meta)
        elif ntype == "end":
            node = EndNode(id=nid, name=name, metadata=meta)
        elif ntype == "task":
            node = TaskNode(
                id=nid,
                name=name,
                metadata=meta,
                task_ref=an.get("task_ref", nid),
                timeout_seconds=an.get("timeout_seconds", None),
                supports_parallel=an.get("supports_parallel", False),
            )
        elif ntype == "decision":
            node = DecisionNode(
                id=nid,
                name=name,
                metadata=meta,
                condition=an.get("condition", "False"),
            )
        elif ntype == "loop":
            node = LoopNode(
                id=nid,
                name=name,
                metadata=meta,
                iterator_var=an.get("iterator_var", f"it_{nid}"),
                iterable_expr=an.get("iterable_expr", ""),
                max_iterations=an.get("max_iterations", 1000),
                continue_on_error=an.get("continue_on_error", False),
            )
        elif ntype == "retry":
            node = RetryNode(
                id=nid,
                name=name,
                metadata=meta,
                attempts=an.get("attempts", 3),
                backoff_seconds=an.get("backoff_seconds", 1.0),
                child_node_id=an.get("child_node_id"),
            )
        elif ntype == "noop":
            node = NoOpNode(id=nid, name=name, metadata=meta)
        else:
            logger.warning("Unknown node type in AST compiler: %s -> treating as NoOp", ntype)
            node = NoOpNode(id=nid, name=name, metadata=meta)

        cfg.add_node(node)
        node_list.append((nid, an))

    # entry/exit detection
    if hasattr(ast, "entry"):
        cfg.entry = getattr(ast, "entry")
    elif isinstance(ast, dict) and "entry" in ast:
        cfg.entry = ast["entry"]
    else:
        if node_list:
            cfg.entry = node_list[0][0]

    if hasattr(ast, "exit"):
        cfg.exit = getattr(ast, "exit")
    elif isinstance(ast, dict) and "exit" in ast:
        cfg.exit = ast["exit"]
    else:
        if node_list:
            cfg.exit = node_list[-1][0]

    # Build var->producers map and node->consumes
    var_producers: Dict[str, Set[str]] = {}
    node_consumes: Dict[str, Set[str]] = {}

    for nid, an in node_list:
        produces = set(an.get("produces", []) or [])
        consumes = set(an.get("consumes", []) or [])
        for v in produces:
            var_producers.setdefault(v, set()).add(nid)
        node_consumes[nid] = consumes

    if var_producers:
        # create edges from each producer to each consumer that consumes the var
        created_edges = set()
        for nid, consumes in node_consumes.items():
            if not consumes:
                continue
            for v in consumes:
                producers = var_producers.get(v, set())
                for p in producers:
                    if p == nid:
                        continue
                    key = (p, nid)
                    if key in created_edges:
                        continue
                    cfg.add_edge(p, nid, label=None)
                    created_edges.add(key)

        # ensure nodes with no outgoing edges flow to exit for proper termination
        for nid, _ in node_list:
            succs = cfg.get_successors(nid)
            if not succs and cfg.exit and nid != cfg.exit:
                cfg.add_edge(nid, cfg.exit, label="next")
    else:
        # fallback: sequential linking as a conservative option
        prev = None
        for nid, _ in node_list:
            if prev is not None:
                cfg.add_edge(prev, nid, label="next")
            prev = nid

    # allow orphan nodes (some ASTs intentionally include helpers)
    cfg.validate_integrity(allow_orphan_nodes=True)
    return cfg


def _compile_stmt(stmt: ASTNode, ctx: CompileContext, prev_id: str) -> str:
    """
    Compile a single statement and return the id of the last node produced for chaining.
    """
    if isinstance(stmt, ASTAssign):
        # RHS must be a call (task)
        if isinstance(stmt.value, ASTTaskCall):
            node_id = _create_tasknode_from_call(stmt.value, ctx, assign_var=stmt.target)
            ctx.cfg.add_edge(prev_id, node_id, label="next")
            return node_id
        else:
            # For now, we only allow assignments of task calls
            raise ValueError(f"Unsupported assignment RHS at line {stmt.lineno}: only task(...) assignments are allowed.")
    elif isinstance(stmt, ASTTaskCall):
        node_id = _create_tasknode_from_call(stmt, ctx, assign_var=None)
        ctx.cfg.add_edge(prev_id, node_id, label="next")
        return node_id
    elif isinstance(stmt, ASTIf):
        return _compile_if(stmt, ctx, prev_id)
    elif isinstance(stmt, ASTFor):
        return _compile_for(stmt, ctx, prev_id)
    elif isinstance(stmt, ASTRetry := ASTRetry):
        return _compile_retry(ASTRetry, ctx, prev_id)
    else:
        raise ValueError(f"Unsupported statement type in compiler: {type(stmt)}")


def _create_tasknode_from_call(call: ASTTaskCall, ctx: CompileContext, assign_var: Optional[str]) -> str:
    """
    Create a TaskNode from an ASTTaskCall. The actual Task (titan.schemas.task.Task) should
    be constructed by the planner; here we set `task_ref` to a deterministic placeholder (either
    the assignment var or a generated id) and store original DSL args in metadata.
    """
    # Determine a task_ref id (planner will map this to a real Task)
    if assign_var:
        task_ref = assign_var
    else:
        task_ref = f"task_{uuid4().hex[:8]}"

    nid = _node_id("task", ctx.counter)
    # build TaskNode with metadata preserving DSL
    tn = TaskNode(id=nid, name=f"task:{call.name}", task_ref=task_ref, metadata={"dsl_call": {"name": call.name, "args": _serialize_call_args(call.args)}})
    ctx.cfg.add_node(tn)
    # record mapping if assigned to var
    if assign_var:
        ctx.var_node_map[assign_var] = nid
    return nid


def _compile_if(node: ASTIf, ctx: CompileContext, prev_id: str) -> str:
    # create decision node
    nid = _node_id("dec", ctx.counter)
    dn = DecisionNode(id=nid, name="decision", condition=node.condition.text, metadata={"source_lineno": node.lineno})
    ctx.cfg.add_node(dn)
    ctx.cfg.add_edge(prev_id, nid, label="next")

    # compile body
    # body entry
    body_prev = nid
    body_entry_id = None
    for stmt in node.body:
        body_prev = _compile_stmt(stmt, ctx, body_prev)
        if body_entry_id is None:
            # the first node after dn is the true-branch entry (we can detect by successor of dn later)
            body_entry_id = ctx.cfg.get_successors(nid)[0] if ctx.cfg.get_successors(nid) else None

    if body_entry_id is None:
        # place a NoOp to represent an empty branch
        noop_id = _node_id("noop", ctx.counter)
        noop = NoOpNode(id=noop_id, name="noop_true")
        ctx.cfg.add_node(noop)
        ctx.cfg.add_edge(nid, noop_id, label="true")
        body_prev = noop_id
    else:
        # ensure edge from decision -> first body node labeled true (find first successor after adding)
        # If not present, add explicit edge
        succs = ctx.cfg.get_successors(nid)
        if not succs:
            ctx.cfg.add_edge(nid, body_entry_id, label="true")
        else:
            # label existing edge true if unlabeled (we keep deterministic label)
            pass

    # compile orelse (false branch)
    if node.orelse:
        false_prev = nid
        false_entry_id = None
        for stmt in node.orelse:
            false_prev = _compile_stmt(stmt, ctx, false_prev)
            if false_entry_id is None:
                false_entry_id = ctx.cfg.get_successors(nid)[0] if ctx.cfg.get_successors(nid) else None
        if false_entry_id is None:
            noop_id = _node_id("noop", ctx.counter)
            noop = NoOpNode(id=noop_id, name="noop_false")
            ctx.cfg.add_node(noop)
            ctx.cfg.add_edge(nid, noop_id, label="false")
            false_prev = noop_id
    else:
        # add a NoOp for the false branch to keep graph structured
        noop_id = _node_id("noop", ctx.counter)
        noop = NoOpNode(id=noop_id, name="noop_false")
        ctx.cfg.add_node(noop)
        ctx.cfg.add_edge(nid, noop_id, label="false")
        false_prev = noop_id

    # after both branches, create join NoOp
    join_id = _node_id("noop", ctx.counter)
    join = NoOpNode(id=join_id, name="join")
    ctx.cfg.add_node(join)
    ctx.cfg.add_edge(body_prev, join.id, label="next")
    ctx.cfg.add_edge(false_prev, join.id, label="next")
    return join.id


def _compile_for(node: ASTFor, ctx: CompileContext, prev_id: str) -> str:
    # Create a loop control node
    loop_id = _node_id("loop", ctx.counter)
    ln = LoopNode(id=loop_id, name="loop", iterator_var=node.iterator, iterable_expr=node.iterable.text, metadata={"source_lineno": node.lineno})
    ctx.cfg.add_node(ln)
    ctx.cfg.add_edge(prev_id, loop_id, label="next")

    # compile body - entry is loop body
    body_prev = loop_id
    body_entry_id = None
    for stmt in node.body:
        body_prev = _compile_stmt(stmt, ctx, body_prev)
        if body_entry_id is None:
            # detect first added node after loop node:
            succs = ctx.cfg.get_successors(loop_id)
            if succs:
                body_entry_id = succs[0]

    if body_entry_id is None:
        # empty body -> NoOp
        noop_id = _node_id("noop", ctx.counter)
        noop = NoOpNode(id=noop_id, name="noop_loop_body")
        ctx.cfg.add_node(noop)
        ctx.cfg.add_edge(loop_id, noop_id, label="body")
        body_prev = noop_id

    # Add edge from body end back to loop node (iterate) and an exit edge
    ctx.cfg.add_edge(body_prev, loop_id, label="continue")
    exit_noop_id = _node_id("noop", ctx.counter)
    exit_noop = NoOpNode(id=exit_noop_id, name="loop_exit")
    ctx.cfg.add_node(exit_noop)
    ctx.cfg.add_edge(loop_id, exit_noop_id, label="break")
    return exit_noop_id


def _compile_retry(node: ASTRetry, ctx: CompileContext, prev_id: str) -> str:
    # Create retry wrapper node
    retry_id = _node_id("retry", ctx.counter)
    rn = RetryNode(id=retry_id, name="retry", attempts=node.attempts, backoff_seconds=node.backoff if node.backoff is not None else 1.0, metadata={"source_lineno": node.lineno})
    ctx.cfg.add_node(rn)
    ctx.cfg.add_edge(prev_id, retry_id, label="next")

    # compile body under retry wrapper; treat first compiled node as child_node_id
    body_prev = retry_id
    child_entry_id = None
    for stmt in node.body:
        body_prev = _compile_stmt(stmt, ctx, body_prev)
        if child_entry_id is None:
            # attempt to detect first child node by successors of retry (if any)
            succs = ctx.cfg.get_successors(retry_id)
            if succs:
                child_entry_id = succs[0]
    if child_entry_id is None:
        # empty retry -> NoOp child
        noop_id = _node_id("noop", ctx.counter)
        noop = NoOpNode(id=noop_id, name="noop_retry_body")
        ctx.cfg.add_node(noop)
        ctx.cfg.add_edge(retry_id, noop_id, label="child")
        child_entry_id = noop_id
        body_prev = noop_id

    # after the child, add success edge to continue and a failure edge to the retry wrapper
    success_noop_id = _node_id("noop", ctx.counter)
    success_noop = NoOpNode(id=success_noop_id, name="retry_success")
    ctx.cfg.add_node(success_noop)
    ctx.cfg.add_edge(body_prev, success_noop_id, label="next")

    # For clarity, set retry.child_node_id in metadata
    rn.child_node_id = child_entry_id
    rn.metadata.setdefault("child_node_id", child_entry_id)
    return success_noop_id


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

# Path: FLOW/titan/executor/loop_engine.py
from __future__ import annotations
from typing import List, Any, Optional, Dict
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class LoopEngine:
    """
    Loop engine with per-iteration error handling.
    Use execute_iterations(loop_node, body_executor_fn, run_context).
    """

    def __init__(self, condition_evaluator, state_tracker, max_iterations_default: int = 1000):
        self.cond = condition_evaluator
        self.state = state_tracker
        self.max_iterations_default = max_iterations_default

    def resolve_iterations(self, loop_node, run_context) -> List[Dict[str, Any]]:
        iterable_expr = getattr(loop_node, "iterable_expr", None)
        if not iterable_expr:
            return []

        try:
            items = self.cond.eval_iterable(iterable_expr)
            if not items:
                return []
            max_it = getattr(loop_node, "max_iterations", self.max_iterations_default) or self.max_iterations_default
            if len(items) > max_it:
                raise ValueError(f"Loop iterable exceeds max_iterations ({len(items)} > {max_it})")
            out = []
            for idx, item in enumerate(items):
                if idx >= max_it:
                    break
                var_name = getattr(loop_node, "iterator_var", f"it_{idx}")
                out.append({
                    "index": idx,
                    "var_name": var_name,
                    "value": item,
                    "timestamp": datetime.utcnow().isoformat(),
                    "loop_node_id": loop_node.id,
                })
            return out
        except Exception as e:
            logger.exception("LoopEngine: failed to resolve iterable: %s", e)
            raise

    def execute_iterations(self, loop_node, body_executor_fn, run_context) -> Dict[str, Any]:
        """
        Execute the loop body for each iteration.
        body_executor_fn(iter_ctx) -> dict with at least 'success': bool
        Honors loop_node.continue_on_error
        Records partial failures into StateTracker
        """
        iterations = self.resolve_iterations(loop_node, run_context)
        results = []
        partial_failures = []
        for it in iterations:
            try:
                res = body_executor_fn(it)
                results.append(res)
                if not res.get("success", False):
                    partial_failures.append({"iteration": it["index"], "error": res.get("error", "iteration_failed"), "result": res})
                    if not getattr(loop_node, "continue_on_error", False):
                        self.state.set_failed(loop_node.id, error=f"iteration_failed_at_{it['index']}")
                        return {"success": False, "completed": len(results), "partial_failures": partial_failures}
                    # else continue
            except Exception as e:
                logger.exception("Loop iteration exception: %s", e)
                partial_failures.append({"iteration": it["index"], "error": str(e)})
                # write partial failure into state tracker
                self.state.set_failed(loop_node.id, error=f"partial_failure at iteration {it['index']}: {e}")
                if not getattr(loop_node, "continue_on_error", False):
                    return {"success": False, "completed": len(results), "partial_failures": partial_failures}
                # else continue
        if partial_failures:
            self.state.set_success(loop_node.id, result={"success": True, "completed": len(results), "partial_failures": partial_failures})
            return {"success": True, "completed": len(results), "partial_failures": partial_failures}
        return {"success": True, "completed": len(results)}

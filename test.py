# Path: test.py
import sys
import os
import logging
import traceback
import textwrap
from typing import Any, Dict

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("TITAN_TEST")

# Add project root
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# --- COLOR CODES ---
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

def log_pass(component: str):
    print(f"{GREEN}[PASS] {component}{RESET}")

def log_fail(component: str, error: Exception):
    print(f"{RED}[FAIL] {component}{RESET}")
    print(f"  -> {str(error)}")
    traceback.print_exc()

def log_header(title: str):
    print(f"\n{'='*60}\n NOW TESTING: {title} \n{'='*60}")

# ==================================================================================
# 1. KERNEL TEST
# ==================================================================================
def test_kernel_boot():
    log_header("KERNEL & RUNTIME")
    try:
        # We import AppContext directly to avoid triggering the Kernel startup cycle
        from titan.kernel.app_context import AppContext
        ctx = AppContext()
        ctx.register("test", lambda: "ok")
        assert ctx.get("test") == "ok"
        assert ctx.get("missing", default=None) is None
        log_pass("Kernel Boot & AppContext Logic")
    except Exception as e:
        log_fail("Kernel Boot", e)

# ==================================================================================
# 2. MEMORY TEST
# ==================================================================================
def test_memory_subsystem():
    log_header("MEMORY SUBSYSTEM")
    try:
        from titan.memory.persistent_annoy_store import PersistentAnnoyStore
        from titan.schemas.memory import MemoryRecord
        
        store = PersistentAnnoyStore(
            index_path="data/test_index.ann", 
            meta_db_path="data/test_meta.db",
            vector_dim=4
        )
        rec = MemoryRecord(id="m1", text="test", embedding=[0.1, 0.2, 0.3, 0.4])
        store.add(rec)
        res = store.query_by_embedding([0.1, 0.2, 0.3, 0.4], top_k=1)
        if not res: raise ValueError("Query failed")
        
        log_pass("Memory Protocol & Persistence")
        store.close()
        # cleanup
        for f in ["data/test_index.ann", "data/test_meta.db"]:
            if os.path.exists(f): os.remove(f)
    except Exception as e:
        log_fail("Memory Subsystem", e)

# ==================================================================================
# 3. PLANNER TEST
# ==================================================================================
def test_planner_compiler():
    log_header("PLANNER (DSL -> AST -> CFG)")
    try:
        from titan.planner.dsl.ir_dsl import parse_dsl
        from titan.planner.dsl.ir_compiler import compile_ast_to_cfg
        
        # DSL Sample with clear 4-space indentation
        dsl_sample = """t1 = task(name="fetch_data")
if t1.result.success:
    t2 = task(name="process_data", data=t1.result.data)
else:
    t3 = task(name="log_error")

for x in t2.result.items:
    t4 = task(name="upload", item=x)
"""
        ast = parse_dsl(dsl_sample)
        cfg = compile_ast_to_cfg(ast)
        
        if len(cfg.nodes) < 5:
            raise ValueError(f"CFG too small: {len(cfg.nodes)} nodes")
            
        log_pass("DSL Grammar & CFG Compilation")
    except Exception as e:
        log_fail("Planner/Compiler", e)

# ==================================================================================
# 4. NEGOTIATOR TEST
# ==================================================================================
def test_negotiator():
    log_header("NEGOTIATOR & BACKEND")
    try:
        from titan.augmentation.negotiator import Negotiator
        from titan.kernel.capability_registry import CapabilityRegistry
        
        class MockSandbox:
            def run(self, cmd, timeout=None, env=None):
                return {"stdout": "ok", "returncode": 0}

        reg = CapabilityRegistry()
        reg.register("sandbox", MockSandbox())
        neg = Negotiator(reg)
        
        res = neg.choose_and_execute({
            "type": "exec",
            "command": "echo hi",
            "timeout_seconds": 1
        })
        if res.get("returncode") != 0:
            raise ValueError(f"Execution failed: {res}")
            
        log_pass("Negotiator Routing & Execution")
    except Exception as e:
        log_fail("Negotiator", e)

# ==================================================================================
# 5. ORCHESTRATOR TEST
# ==================================================================================
def test_orchestrator_loop():
    log_header("ORCHESTRATOR LOOP")
    try:
        from titan.executor.orchestrator import Orchestrator
        from titan.schemas.plan import Plan, PlanStatus
        from titan.schemas.graph import CFG, StartNode, EndNode, TaskNode
        
        cfg = CFG()
        start = StartNode(id="start")
        task = TaskNode(id="t1", name="task:echo", task_ref="t1")
        end = EndNode(id="end")
        
        cfg.add_node(start)
        cfg.add_node(task)
        cfg.add_node(end)
        cfg.add_edge("start", "t1", label="next")
        cfg.add_edge("t1", "end", label="next")
        cfg.entry = "start"
        cfg.exit = "end"
        
        plan = Plan(
            dsl_text="", 
            parsed_ast={}, 
            cfg=cfg, 
            status=PlanStatus.CREATED
        )

        def mock_runner(payload):
            return {"status": "success", "success": True}

        orch = Orchestrator(runner=mock_runner)
        summary = orch.execute_plan(plan, session_id="test")
        
        if summary.get("status") != "success":
            raise ValueError(f"Orchestrator failed: {summary}")
            
        log_pass("Orchestrator Execution Cycle")
    except Exception as e:
        log_fail("Orchestrator", e)

if __name__ == "__main__":
    test_kernel_boot()
    test_memory_subsystem()
    test_planner_compiler()
    test_negotiator()
    test_orchestrator_loop()
    print("\n" + "="*60 + "\n DIAGNOSTIC COMPLETE \n" + "="*60)
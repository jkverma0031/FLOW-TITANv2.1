# Path: test_e2e_suite.py (FINAL VERSION WITH ROBUST OUTPUT)

import unittest
import sys
import os
import tempfile
import shutil
from typing import Any, Dict, List, Optional, Callable, Tuple, Iterable
from unittest.mock import MagicMock, call, patch
import json
import io # Added for robust output handling

# --- 1. MOCK MINIMAL SCHEMAS & UTILITIES ---

class MockNodeBase:
    def __init__(self, id, name=None, metadata=None):
        self.id = id
        self.name = name
        self.metadata = metadata or {}
        self.type = None
    
    def dict_safe(self):
        return {"id": self.id, "type": str(self.type)}

class MockStartNode(MockNodeBase):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.type = "START"

class MockEndNode(MockNodeBase):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.type = "END"

class MockTaskNode(MockNodeBase):
    def __init__(self, task_ref, timeout_seconds=None, supports_parallel=False, **kwargs):
        super().__init__(**kwargs)
        self.type = "TASK"
        self.task_ref = task_ref
        self.timeout_seconds = timeout_seconds
        self.supports_parallel = supports_parallel

class MockDecisionNode(MockNodeBase):
    def __init__(self, condition, **kwargs):
        super().__init__(**kwargs)
        self.type = "DECISION"
        self.condition = condition

class MockLoopNode(MockNodeBase):
    def __init__(self, iterator_var, iterable_expr, **kwargs):
        super().__init__(**kwargs)
        self.type = "LOOP"
        self.iterator_var = iterator_var
        self.iterable_expr = iterable_expr

class MockRetryNode(MockNodeBase):
    def __init__(self, attempts, backoff_seconds, child_node_id=None, **kwargs):
        super().__init__(**kwargs)
        self.type = "RETRY"
        self.attempts = attempts
        self.backoff_seconds = backoff_seconds
        self.child_node_id = child_node_id

class MockNoOpNode(MockNodeBase):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.type = "NOOP"

class MockCFG:
    def __init__(self):
        self.nodes = {}
        self.edges = []
        self.entry = None
        self.exit = None

    def add_node(self, node: MockNodeBase):
        self.nodes[node.id] = node

    def add_edge(self, source, target, label=None):
        mock_edge = MagicMock(source=source, target=target, label=label)
        mock_edge.source = source
        mock_edge.target = target
        mock_edge.label = label
        self.edges.append(mock_edge)

    def get_successors(self, node_id):
        return [e.target for e in self.edges if e.source == node_id]
        
    def validate_integrity(self, **kwargs):
        return True

class MockNodeType:
    TASK = "TASK"
    DECISION = "DECISION"
    LOOP = "LOOP"
    RETRY = "RETRY"
    NOOP = "NOOP"
    START = "START"
    END = "END"

class MockActionType:
    EXEC = "EXEC"

class MockPlanStatus:
    CREATED = "CREATED"

class MockEvent:
    def __init__(self, type, payload, **kwargs):
        self.type = type
        self.payload = payload

class MockEventType:
    PLAN_CREATED = "PLAN_CREATED"
    NODE_STARTED = "NODE_STARTED"
    NODE_FINISHED = "NODE_FINISHED"
    ERROR_OCCURRED = "ERROR_OCCURRED"
    DECISION_TAKEN = "DECISION_TAKEN"
    PLAN_COMPLETED = "PLAN_COMPLETED"
    
class MockPlan:
    def __init__(self, id, cfg):
        self.id = id
        self.cfg = cfg
        self.status = MockPlanStatus.CREATED
    
    def to_summary(self):
        return {"plan_id": self.id}
    
class MockAction:
    def __init__(self, type, command, args, timeout_seconds, metadata):
        self.type = type
        self.command = command
        self.args = args
        self.metadata = metadata
        self.timeout_seconds = timeout_seconds
        
    def to_exec_payload(self):
        return {"command": self.command, "args": self.args, "metadata": self.metadata}

# Mocking modules required for dynamic imports
sys.modules['titan.schemas.graph'] = MagicMock(CFG=MockCFG, TaskNode=MockTaskNode, DecisionNode=MockDecisionNode, LoopNode=MockLoopNode, RetryNode=MockRetryNode, NoOpNode=MockNoOpNode, StartNode=MockStartNode, EndNode=MockEndNode, NodeType=MockNodeType)
sys.modules['titan.schemas.plan'] = MagicMock(Plan=MockPlan, PlanStatus=MockPlanStatus)
sys.modules['titan.schemas.events'] = MagicMock(Event=MockEvent, EventType=MockEventType)
sys.modules['titan.schemas.action'] = MagicMock(Action=MockAction, ActionType=MockActionType)
sys.modules['titan.schemas.task'] = MagicMock()
sys.modules['titan.observability.tracing'] = MagicMock(tracer=MagicMock(current_trace_id=lambda: "T1", current_span_id=lambda: "S1", span=lambda *a, **kw: MagicMock()))
sys.modules['titan.observability.metrics'] = MagicMock(metrics=MagicMock(counter=lambda *a, **kw: MagicMock(inc=lambda: None), timer=lambda *a, **kw: MagicMock(time=lambda: None)))

class MockStorageAdapter(MagicMock):
    def init(self): pass
    def save_session(self, sid, data): pass
    def load_session(self, sid): return None
    def delete_session(self, sid): pass
    def list_session_ids(self): return []
    def export_all(self): return []
    def close(self): pass

class MockLLM:
    def generate(self, prompt, max_tokens):
        if "generate plan for task A" in prompt:
            return "t1 = task(name=\"step_a\", file=\"a.txt\")\nif t1.result.ok:\n  t2 = task(name=\"step_b\")"
        return ""

def mock_runner(payload: Dict[str, Any]) -> Dict[str, Any]:
    cmd = payload.get('command')
    args = payload.get('args')
    
    if cmd == "initial_task":
        return {"success": True, "result": {"value": 10, "status": "ok"}}
    elif cmd == "conditional_task":
        return {"success": True, "result": {"message": "Executed conditional logic"}}
    elif cmd == "loop_task":
        item_arg = args.get('item')
        item_val = item_arg.value if hasattr(item_arg, 'value') else item_arg
        return {"success": True, "result": {"message": f"Processed item: {item_val}"}}
    else:
        return {"success": True, "result": {"message": f"Executed: {cmd}"}}

# --- 2. DYNAMICALLY IMPORT TITAN MODULES ---
PROJECT_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
sys.path.insert(0, PROJECT_ROOT)

try:
    from titan.planner.dsl.ir_dsl import parse_dsl, ASTIf, ASTTaskCall, ASTAssign, DSLIndenter, GRAMMAR
    from titan.planner.dsl.ir_compiler import compile_ast_to_cfg, CompileContext
    from titan.executor.scheduler import Scheduler
    from titan.executor.orchestrator import Orchestrator
    from titan.executor.condition_evaluator import ConditionEvaluator
    from titan.executor.state_tracker import StateTracker
    from titan.executor.loop_engine import LoopEngine
    from titan.executor.retry_engine import RetryEngine
    from titan.executor.worker_pool import WorkerPool
    from titan.parser.llm_dsl_generator import LLMDslGenerator
    from titan.runtime.session_manager import SessionManager, SQLiteStorageAdapter, DEFAULT_DIR
    from titan.policy.engine import PolicyEngine, PolicyDecision
except ImportError as e:
    print(f"FATAL ERROR: Could not import a Titan module. Error: {e}")
    sys.exit(1)


# --- 3. TEST SUITE IMPLEMENTATION ---

class TestTitanAgentOS(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp()
        
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir)

    def setUp(self):
        self.state_tracker = StateTracker()
        self.worker_pool = WorkerPool(max_workers=1, runner=mock_runner)
        self.llm_mock = MockLLM()
        
        self.emitter_mock = MagicMock()
        self.session_manager = SessionManager(
            storage_adapter=MockStorageAdapter(),
            autosave_context_dir=self.test_dir 
        )
        self.orchestrator = Orchestrator(
            runner=mock_runner,
            event_emitter=self.emitter_mock
        )

    def tearDown(self):
        self.worker_pool.shutdown()

# ----------------------------------------------------------------------
# A. Planner Subsystem Unit Tests (Validating Fixes)
# ----------------------------------------------------------------------

    def test_A1_dsl_parser_attr_access_and_if_block_fix(self):
        """Tests that the critical dot notation and block indentation issues are fixed (Error 1 & Indent Fix)."""
        dsl_source = "t1 = task(name=\"a\")\nif t1.result.ok:\n  t2 = task(name=\"b\")"
        ast = parse_dsl(dsl_source)
        
        self.assertEqual(len(ast.statements), 2)
        self.assertIsInstance(ast.statements[1], ASTIf)
        self.assertIn("t1.result.ok", ast.statements[1].condition.text)
        self.assertEqual(len(ast.statements[1].body), 1)
        self.assertIsInstance(ast.statements[1].body[0], ASTAssign)

    def test_A2_dsl_parser_keyword_arg_fix(self):
        """Tests that keyword arguments are correctly parsed (Bug 2/Failure fix)."""
        dsl_source = "t1 = task(download_url=\"http://example.com\", force=True, cache=5)\n"
        ast = parse_dsl(dsl_source)
        
        self.assertIsInstance(ast.statements[0], ASTAssign)
        call = ast.statements[0].value
        self.assertIsInstance(call, ASTTaskCall)
        self.assertEqual(call.name, "task")
        self.assertEqual(call.args["download_url"].value, "http://example.com")
        self.assertEqual(call.args["force"].value, True)
        self.assertEqual(call.args["cache"].value, 5)

    def test_A3_cfg_compiler_if_else_structure(self):
        """Tests that the compiler creates correct nodes and 'true'/'false' edges (Error 3 fix)."""
        dsl_source = "t0=task(name=\"pre\")\nif t0.result.ok:\n  t1 = task(name=\"then\")\nelse:\n  t2 = task(name=\"else\")"
        ast = parse_dsl(dsl_source)
        
        with patch('titan.planner.dsl.ir_compiler._node_id', side_effect=lambda prefix, counter: f"{prefix}_{next(counter):02d}"):
            cfg = compile_ast_to_cfg(ast)
        
        t0_id = next(nid for nid, node in cfg.nodes.items() if node.name == "task:pre")
        decision_node_id = next(nid for nid, node in cfg.nodes.items() if node.type == MockNodeType.DECISION)
        t1_id = next(nid for nid, node in cfg.nodes.items() if node.name == "task:then")
        t2_id = next(nid for nid, node in cfg.nodes.items() if node.name == "task:else")
        join_id = next(nid for nid, node in cfg.nodes.items() if node.name == "join")

        self.assertTrue(any(e for e in cfg.edges if e.source == t0_id and e.target == decision_node_id and e.label == "next"))
        
        true_edge = next(e for e in cfg.edges if e.source == decision_node_id and e.target == t1_id and e.label == "true")
        false_edge = next(e for e in cfg.edges if e.source == decision_node_id and e.target == t2_id and e.label == "false")
        
        self.assertIsNotNone(true_edge, "Decision Node 'true' branch failed.")
        self.assertIsNotNone(false_edge, "Decision Node 'false' branch failed.")

        self.assertTrue(any(e for e in cfg.edges if e.source == t1_id and e.target == join_id), "True branch end must connect to join.")
        self.assertTrue(any(e for e in cfg.edges if e.source == t2_id and e.target == join_id), "False branch end must connect to join.")


# ----------------------------------------------------------------------
# B. Policy Subsystem Unit Tests (Validating Security and Deny Fix)
# ----------------------------------------------------------------------

    def test_B1_policy_engine_deep_arg_check_and_default_allow(self):
        """Tests that the recursive safety check for forbidden strings is working AND adds an explicit default allow rule (Fail fix)."""
        engine = PolicyEngine()
        forbidden_list = ["rm -rf", "delete_all", "curl http"]
        
        engine.add_rule(PolicyEngine.rule_deny_if_command_contains_forbidden(forbidden_list))

        def rule_allow_safe_defaults(ctx):
             action = ctx.get("action")
             if action and action.command not in ["safe_cmd", "bad_cmd_1", "bad_cmd_2"]:
                 return PolicyDecision(True, reason="Default allow for benign commands")
             return None
             
        engine.add_rule(rule_allow_safe_defaults)

        # Scenario 1: Command injection in argument (should fail)
        action1 = MockAction(MockActionType.EXEC, command="safe_cmd", 
                            args={"content": "payload; delete_all"}, 
                            timeout_seconds=60, metadata={})
        decision1 = engine.check(action1)
        self.assertFalse(decision1.allow, "Policy failed to catch command injection hidden in 'args'.")
        self.assertIn("delete_all", decision1.reason)

        # Scenario 2: Safe action (should pass due to explicit allow rule)
        action2 = MockAction(MockActionType.EXEC, command="safe_process", 
                            args={"path": "/var/log/file.txt"}, 
                            timeout_seconds=60, metadata={})
        decision2 = engine.check(action2)
        self.assertTrue(decision2.allow, "Policy incorrectly denied a safe command despite explicit allow rule.")

# ----------------------------------------------------------------------
# C. E2E Integration Test (Plan -> Orchestrator -> Scheduler -> Execution)
# ----------------------------------------------------------------------

    @patch('titan.planner.dsl.ir_compiler._node_id', side_effect=lambda prefix, counter: f"{prefix}_{next(counter):06d}")
    def test_C1_e2e_full_planning_and_execution_flow(self, mock_node_id):
        """Tests full end-to-end integration: Planner pipeline output -> Orchestrator execution (Error 4 fix)."""
        
        # 1. SETUP PLAN
        dsl_source = (
            "t1 = task(name=\"initial_task\")\n"
            "if t1.result.value > 5:\n"
            "  t2 = task(name=\"conditional_task\")\n"
            "  for item in [1, 2]:\n"
            "    t3 = task(name=\"loop_task\", item=item)\n"
        )
        ast = parse_dsl(dsl_source)
        cfg = compile_ast_to_cfg(ast)
        plan = MockPlan(id="test_plan_123", cfg=cfg)

        # 2. EXECUTE PLAN
        session_id = "test_session_456"
        summary = self.orchestrator.execute_plan(plan, session_id)

        # 3. VERIFY RESULTS AND FLOW
        self.assertEqual(summary["status"], "success", f"E2E execution failed unexpectedly: {summary.get('error')}")
        
        self.assertEqual(self.worker_pool.submit.call_count, 4, "Worker pool call count mismatch (Expected 4 task submissions: t1 + t2 + t3x2).")
        
        loop_calls = [c for c in self.worker_pool.submit.call_args_list if c.args[0]['command'] == 'loop_task']
        
        self.assertEqual(loop_calls[0].args[0]['args']['item'].value, 1)
        self.assertEqual(loop_calls[1].args[0]['args']['item'].value, 2)
        
        emitted_events = self.emitter_mock.call_args_list
        self.assertTrue(any(call.args[0].type == MockEventType.PLAN_CREATED for call in emitted_events))
        self.assertTrue(any(call.args[0].type == MockEventType.DECISION_TAKEN for call in emitted_events))
        self.assertTrue(any(call.args[0].type == MockEventType.PLAN_COMPLETED for call in emitted_events))


# --- FINAL EXECUTION BLOCK ---
if __name__ == '__main__':
    # FIX: Manually creating a TestRunner instance to guarantee console output
    print("Running FLOW-TITANv2.1 E2E/Integration Test Suite...")
    
    # Use TextTestRunner with high verbosity (2) to ensure detailed output.
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    suite = unittest.TestLoader().loadTestsFromTestCase(TestTitanAgentOS)
    
    # Run the suite
    result = runner.run(suite)
    
    # Custom summary print
    print("\n--- TEST EXECUTION SUMMARY ---")
    print(f"Total Tests Run: {result.testsRun}")
    if result.wasSuccessful():
        print("✅ ALL TESTS PASSED: The core Planner and Executor components are functioning correctly.")
    else:
        print(f"❌ FAILURES/ERRORS: {len(result.failures) + len(result.errors)}")
        print("Review the detailed output above for component breakdowns.")
        
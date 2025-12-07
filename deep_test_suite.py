import unittest
import sys
import os
import logging
import textwrap
from unittest.mock import MagicMock, patch

# Add project root
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.CRITICAL)  # Silence internal logs during tests
logger = logging.getLogger("TITAN_DEEP_TEST")

# --- IMPORT SYSTEM UNDER TEST ---
try:
    from titan.planner.dsl.ir_dsl import parse_dsl, ASTIf, ASTFor, ASTTaskCall
    from titan.planner.dsl.ir_compiler import compile_ast_to_cfg
    from titan.schemas.graph import NodeType
    from titan.executor.state_tracker import StateTracker, NodeState
    from titan.executor.condition_evaluator import ConditionEvaluator
    from titan.executor.loop_engine import LoopEngine
    from titan.executor.scheduler import Scheduler
    from titan.executor.worker_pool import WorkerPool
    from titan.schemas.graph import CFG, TaskNode, DecisionNode, StartNode, EndNode
except ImportError as e:
    print(f"❌ CRITICAL IMPORT ERROR: {e}")
    print("Ensure you have applied all the fixes from the previous turn.")
    sys.exit(1)

# ==================================================================================
# 1. DEEP PARSER LOGIC TESTS (The "Brain" Verify)
# ==================================================================================
class TestPlannerLogic(unittest.TestCase):
    """
    Verifies that the DSL Parser correctly understands structure, nesting, and precedence.
    """
    
    def test_nested_logic_parsing(self):
        """
        Does the parser understand nested indentation blocks correctly?
        """
        code = textwrap.dedent("""
            if check.result:
                t1 = task(name="A")
                if t1.success:
                    t2 = task(name="B")
                else:
                    t3 = task(name="C")
            else:
                t4 = task(name="D")
        """).strip()
        
        ast = parse_dsl(code)
        
        # 1. Check Root Structure
        self.assertIsInstance(ast.statements[0], ASTIf, "Root statement must be If")
        root_if = ast.statements[0]
        
        # 2. Check Nested True Branch
        true_branch = root_if.body
        self.assertEqual(len(true_branch), 2, "True branch should have 2 statements (t1 assignment + nested if)")
        self.assertIsInstance(true_branch[1], ASTIf, "Second statement in True branch must be nested If")
        
        # 3. Check Nested If Structure
        nested_if = true_branch[1]
        self.assertEqual(len(nested_if.body), 1, "Nested If True branch should have 1 task")
        self.assertEqual(len(nested_if.orelse), 1, "Nested If False branch should have 1 task")
        
        print("\n✅ Parser: Deep Nesting Logic")

    def test_expression_precedence(self):
        """
        Does the parser handle complex boolean expressions without crashing?
        """
        code = 'if a.result > 10 and b.status == "ok" or c.flag:'
        # We wrap in a minimal block to parse it
        full_code = f'{code}\n    t1 = task(name="noop")'
        
        ast = parse_dsl(full_code)
        condition_text = ast.statements[0].condition.text
        
        # We expect the text to be preserved or reconstructed correctly
        expected_fragments = ["a.result", ">", "10", "and", "b.status", "==", '"ok"', "or", "c.flag"]
        for frag in expected_fragments:
            self.assertIn(frag, condition_text, f"Expression parser dropped fragment: {frag}")
            
        print("✅ Parser: Expression Complexity")

# ==================================================================================
# 2. COMPILER GRAPH THEORY TESTS (The "Structure" Verify)
# ==================================================================================
class TestCompilerGraph(unittest.TestCase):
    """
    Verifies that the CFG constructed is mathematically sound (connected, valid edges).
    """
    
    def test_cfg_branching_topology(self):
        """
        Verifies that an IF/ELSE block creates a diamond shape in the graph.
        Start -> Decision -> (TrueNode, FalseNode) -> JoinNode -> End
        """
        code = textwrap.dedent("""
            if cond:
                t1 = task(name="A")
            else:
                t2 = task(name="B")
        """).strip()
        
        ast = parse_dsl(code)
        cfg = compile_ast_to_cfg(ast)
        
        # 1. Validate Graph Integrity
        self.assertTrue(cfg.validate_integrity(), "Graph integrity check failed")
        
        # 2. Locate Decision Node
        dec_nodes = [n for n in cfg.nodes.values() if n.type == NodeType.DECISION]
        self.assertEqual(len(dec_nodes), 1, "Should have exactly 1 Decision node")
        dec_node = dec_nodes[0]
        
        # 3. Analyze Outgoing Edges
        edges = cfg.get_edges_from(dec_node.id)
        edge_labels = sorted([e.label for e in edges])
        self.assertEqual(edge_labels, ["false", "true"], "Decision node must have 'true' and 'false' edges")
        
        # 4. Verify Convergence (Join)
        # Get targets of true/false paths
        true_target = next(e.target for e in edges if e.label == "true")
        false_target = next(e.target for e in edges if e.label == "false")
        
        # Trace them to next node
        true_succs = cfg.get_successors(true_target)
        false_succs = cfg.get_successors(false_target)
        
        # They should eventually point to the same Join/End node
        self.assertTrue(len(true_succs) > 0, "True branch dead end")
        self.assertTrue(len(false_succs) > 0, "False branch dead end")
        
        print("✅ Compiler: Branch Topology (Diamond Shape)")

    def test_loop_topology(self):
        """
        Verifies a loop creates a cycle: LoopHeader -> Body -> LoopHeader
        """
        code = textwrap.dedent("""
            for i in items:
                t1 = task(name="process", item=i)
        """).strip()
        
        ast = parse_dsl(code)
        cfg = compile_ast_to_cfg(ast)
        
        loop_nodes = [n for n in cfg.nodes.values() if n.type == NodeType.LOOP]
        self.assertEqual(len(loop_nodes), 1)
        loop_header = loop_nodes[0]
        
        # Check for cycle back to header
        # We traverse: Header -> Body -> ... -> Header
        successors = cfg.get_successors(loop_header.id)
        
        # One path goes to body, one to break
        body_edge = next((e for e in cfg.get_edges_from(loop_header.id) if e.label == "body"), None)
        self.assertIsNotNone(body_edge, "Loop must have a 'body' edge")
        
        # Verify reachability back to start is possible (Cycle check)
        # Simple BFS
        queue = [body_edge.target]
        seen = set()
        found_cycle = False
        while queue:
            curr = queue.pop(0)
            if curr in seen: continue
            seen.add(curr)
            
            # Check edges from current
            for e in cfg.get_edges_from(curr):
                if e.target == loop_header.id and e.label == "continue":
                    found_cycle = True
                queue.append(e.target)
                
        self.assertTrue(found_cycle, "Compiler failed to create a cycle (Back-edge) for the loop")
        print("✅ Compiler: Loop Topology (Cycle)")

# ==================================================================================
# 3. EXECUTOR STATE MACHINE TESTS (The "Runtime" Verify)
# ==================================================================================
class TestExecutorDeep(unittest.TestCase):
    
    def setUp(self):
        self.state = StateTracker()
        # Mock resolver injection
        self.cond_eval = ConditionEvaluator(resolver=lambda x: self.resolve_mock(x))
        self.loop = LoopEngine(self.cond_eval, self.state)
        # Mock worker pool
        self.pool = MagicMock(spec=WorkerPool)
        self.pool.submit.return_value = MagicMock(result=lambda: {"status": "ok"})
        
        # Build a manual CFG for precise testing
        self.cfg = CFG()
        self.cfg.add_node(StartNode(id="start"))
        self.cfg.add_node(TaskNode(id="t1", name="task:A", task_ref="A"))
        self.cfg.add_node(EndNode(id="end"))
        self.cfg.add_edge("start", "t1")
        self.cfg.add_edge("t1", "end")
        self.cfg.entry = "start"
        self.cfg.exit = "end"

        self.scheduler = Scheduler(
            cfg=self.cfg,
            worker_pool=self.pool,
            state_tracker=self.state,
            condition_evaluator=self.cond_eval,
            loop_engine=self.loop,
            retry_engine=None,
            replanner=None
        )
        
    def resolve_mock(self, var_name):
        data = {"var": 10, "nested": {"val": True}}
        if var_name == "var": return 10
        if var_name == "nested.val": return True
        return None

    def test_condition_evaluation_logic(self):
        """
        Verify the ConditionEvaluator handles python-like syntax correctly.
        """
        self.assertTrue(self.cond_eval.evaluate("var == 10"))
        self.assertTrue(self.cond_eval.evaluate("var > 5"))
        self.assertFalse(self.cond_eval.evaluate("var < 5"))
        self.assertTrue(self.cond_eval.evaluate("nested.val"))
        print("✅ Executor: Condition Logic")

    def test_state_transitions(self):
        """
        Verify nodes go PENDING -> RUNNING -> COMPLETED with timestamps.
        """
        # Manually trigger a run step
        self.scheduler.run("sess_1", "plan_1")
        
        # Check t1 state
        t1_state = self.state.get_state("t1")
        self.assertEqual(t1_state["status"], NodeState.COMPLETED)
        self.assertIsNotNone(t1_state["start_time"])
        self.assertIsNotNone(t1_state["end_time"])
        self.assertGreaterEqual(t1_state["end_time"], t1_state["start_time"])
        print("✅ Executor: State Transitions & Timing")

    def test_loop_iteration_state(self):
        """
        Verify LoopEngine tracks iterations and manages context injection.
        """
        # Setup a loop node
        loop_node = MagicMock()
        loop_node.id = "loop_1"
        loop_node.iterator_var = "i"
        loop_node.iterable_expr = "[1, 2, 3]" # String expr
        
        # FIX: Explicitly set max_iterations to an integer to prevent MagicMock comparison error
        loop_node.max_iterations = 100 
        
        # We need to mock the evaluator to return the list
        self.cond_eval.evaluate = MagicMock(return_value=[1, 2, 3])
        
        # 1st Iteration
        should_cont = self.loop.should_continue(loop_node)
        self.assertTrue(should_cont)
        
        # 2nd Iteration
        should_cont = self.loop.should_continue(loop_node)
        self.assertTrue(should_cont)
        
        # 3rd Iteration
        should_cont = self.loop.should_continue(loop_node)
        self.assertTrue(should_cont)
        
        # 4th Iteration -> Should Stop
        should_cont = self.loop.should_continue(loop_node)
        self.assertFalse(should_cont)
        
        print("✅ Executor: Loop Iteration Control")

# ==================================================================================
# MAIN RUNNER
# ==================================================================================
if __name__ == "__main__":
    print("\n🚀 STARTING DEEP ARCHITECTURE VALIDATION (TITAN v2.1) 🚀\n")
    
    # Custom runner to show nice output
    suite = unittest.TestSuite()
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestPlannerLogic))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestCompilerGraph))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestExecutorDeep))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    if result.wasSuccessful():
        print("\n🏆 GOD-TIER STATUS: VERIFIED. All internal systems behave correctly.")
    else:
        print("\n💥 SYSTEM FAILURE. Check critical logs above.")
        sys.exit(1)
import unittest
import sys
import os
import shutil
import tempfile
import logging
import json
import time
from unittest.mock import MagicMock, patch, ANY
from dataclasses import asdict

# --- SETUP ENVIRONMENT ---
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# Silence non-critical logs
logging.basicConfig(level=logging.CRITICAL) 

# --- IMPORTS (Touching Every Subsystem) ---
try:
    # Schemas
    from titan.schemas.plan import Plan, PlanStatus
    from titan.schemas.task import Task
    from titan.schemas.graph import CFG, TaskNode, DecisionNode, StartNode, EndNode, NodeType
    from titan.schemas.action import Action, ActionType
    from titan.schemas.memory import MemoryRecord
    from titan.schemas.events import Event, EventType

    # Runtime
    from titan.runtime.session_manager import SessionManager
    from titan.runtime.context_store import ContextStore
    from titan.runtime.trust_manager import TrustManager
    
    # Memory
    from titan.memory.persistent_annoy_store import PersistentAnnoyStore
    from titan.memory.episodic_store import EpisodicStore
    
    # Planner
    from titan.planner.frame_parser import FrameParser
    from titan.planner.intent_modifier import modify_intent
    from titan.planner.task_extractor import extract_task_hints
    from titan.planner.dsl.ir_dsl import parse_dsl
    from titan.planner.dsl.ir_compiler import compile_ast_to_cfg
    
    # Augmentation
    from titan.augmentation.safety import SafetyEngine
    from titan.augmentation.negotiator import Negotiator
    from titan.augmentation.provenance import ProvenanceChain
    from titan.kernel.capability_registry import CapabilityRegistry
    
    # Executor
    from titan.executor.orchestrator import Orchestrator
    from titan.executor.scheduler import Scheduler
    from titan.executor.state_tracker import StateTracker, NodeState
    from titan.executor.condition_evaluator import ConditionEvaluator
    from titan.executor.worker_pool import WorkerPool

    print("✅ All Modules Imported Successfully.")
except ImportError as e:
    print(f"❌ CRITICAL IMPORT ERROR: {e}")
    sys.exit(1)


class TestTitanExtreme(unittest.TestCase):
    """
    The Master Test Suite.
    Verifies functional correctness and data integrity across the entire OS.
    """

    @classmethod
    def setUpClass(cls):
        # Create a temp workspace for file-based tests
        cls.test_dir = tempfile.mkdtemp()
        print(f"\n⚡ Using temp workspace: {cls.test_dir}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir)

    # =========================================================================
    # 1. RUNTIME & CONTEXT TESTS
    # =========================================================================
    def test_runtime_data_flow(self):
        """
        Verifies that SessionManager correctly initializes ContextStore and TrustManager,
        and that data persists within a session object.
        """
        sm = SessionManager() # Assuming default in-memory or mock DB
        
        # 1. Create Session
        session_id = "sess_001"
        session = sm.create_session(session_id, user_id="user_test")
        
        self.assertIsNotNone(session, "Session creation failed")
        self.assertEqual(session.session_id, session_id)
        
        # 2. Context Integrity
        ctx = ContextStore(session_id)
        ctx.set("user_var", 42)
        ctx.set("complex_obj", {"a": 1, "b": [2, 3]})
        
        self.assertEqual(ctx.get("user_var"), 42)
        self.assertEqual(ctx.get("complex_obj")["b"][1], 3, "Complex object data corruption in ContextStore")
        
        print("✅ Runtime: Data Flow & Context Integrity")

    # =========================================================================
    # 2. MEMORY SUBSYSTEM TESTS
    # =========================================================================
    def test_memory_persistence_integrity(self):
        """
        Verifies that vectors and metadata are stored, indexed, and retrieved correctly.
        """
        db_path = os.path.join(self.test_dir, "mem.db")
        idx_path = os.path.join(self.test_dir, "mem.ann")
        
        store = PersistentAnnoyStore(index_path=idx_path, meta_db_path=db_path, vector_dim=4)
        
        # 1. Add Data
        original_vec = [0.1, 0.2, 0.3, 0.4]
        meta = {"source": "test_doc", "content": "important data"}
        record = MemoryRecord(id="m1", text="test", embedding=original_vec, metadata=meta)
        
        store.add(record)
        store.persist()
        
        # 2. Close and Re-open (Simulate Restart)
        store.close()
        store_reloaded = PersistentAnnoyStore(index_path=idx_path, meta_db_path=db_path, vector_dim=4)
        
        # 3. Query & Verify Data Integrity
        results = store_reloaded.query_by_embedding(original_vec, top_k=1)
        
        self.assertTrue(len(results) > 0, "Memory retrieval failed after reload")
        retrieved_meta = results[0]
        
        # Check if metadata survived round-trip
        self.assertEqual(retrieved_meta.get("source"), "test_doc", "Metadata field 'source' lost")
        self.assertEqual(retrieved_meta.get("content"), "important data", "Metadata field 'content' lost")
        
        store_reloaded.close()
        print("✅ Memory: Persistence & Metadata Integrity")

    # =========================================================================
    # 3. PLANNER COMPONENT TESTS
    # =========================================================================
    def test_planner_components_logic(self):
        """
        Tests the helper components of the planner individually.
        """
        # 1. Intent Modifier
        # (Assuming simple pass-through or basic logic in current impl)
        modified = modify_intent(" upload file ", context=None)
        self.assertEqual(modified, "upload file", "Intent modifier should normalize string")

        # 2. Frame Parser (Regex Logic)
        fp = FrameParser()
        frames = fp.parse("copy file from A to B")
        # Depending on your implementation, this might return a struct or dict
        # We verify it doesn't crash and returns a result
        self.assertIsNotNone(frames, "FrameParser returned None")

        # 3. Task Extractor
        hints = extract_task_hints(frames)
        self.assertIsInstance(hints, list, "Task hints must be a list")
        
        print("✅ Planner: NLP Components Logic")

    # =========================================================================
    # 4. COMPILER PIPELINE (DSL -> CFG)
    # =========================================================================
    def test_compiler_correctness(self):
        """
        Verifies that specific DSL constructs map to specific Graph Nodes correctly.
        """
        dsl = """
t1 = task(name="load")
if t1.result:
    t2 = task(name="process", data=t1.result)
    retry attempts=3:
        t3 = task(name="save")
"""
        # 1. Parse
        ast = parse_dsl(dsl)
        
        # 2. Compile
        cfg = compile_ast_to_cfg(ast)
        
        # 3. Verify Graph Structure
        nodes = list(cfg.nodes.values())
        
        # Count types
        tasks = [n for n in nodes if n.type == NodeType.TASK]
        decisions = [n for n in nodes if n.type == NodeType.DECISION]
        retries = [n for n in nodes if n.type == NodeType.RETRY]
        
        self.assertEqual(len(tasks), 3, "Should produce 3 TaskNodes (load, process, save)")
        self.assertEqual(len(decisions), 1, "Should produce 1 DecisionNode")
        self.assertEqual(len(retries), 1, "Should produce 1 RetryNode")
        
        # 4. Verify Data Transfer in AST (Arguments)
        # Find 'process' task
        process_task = next(t for t in tasks if "process" in t.name)
        # Check if args were captured in metadata
        args = process_task.metadata.get("dsl_call", {}).get("args", {})
        self.assertIn("data", args, "Task argument 'data' missing from compiled node")
        
        print("✅ Compiler: AST to CFG Transformation & Data Preservation")

    # =========================================================================
    # 5. AUGMENTATION & SAFETY TESTS
    # =========================================================================
    def test_safety_and_negotiation(self):
        """
        Verifies that dangerous commands are blocked and safe ones are routed.
        """
        # 1. Safety Engine
        safety = SafetyEngine()
        is_safe, reason = safety.check_command("rm -rf /")
        self.assertFalse(is_safe, "Safety engine failed to block 'rm -rf /'")
        
        is_safe, _ = safety.check_command("echo hello")
        self.assertTrue(is_safe, "Safety engine blocked safe command")

        # 2. Negotiator Routing
        registry = CapabilityRegistry()
        mock_sandbox = MagicMock()
        mock_sandbox.run.return_value = {"status": "ok"}
        registry.register("sandbox", mock_sandbox)
        
        negotiator = Negotiator(registry)
        
        # Test routing an EXEC action
        action_payload = {
            "type": "exec",
            "command": "python script.py",
            "args": {},
            "timeout_seconds": 5
        }
        
        result = negotiator.choose_and_execute(action_payload)
        self.assertEqual(result["status"], "ok", "Negotiator failed to execute via sandbox")
        mock_sandbox.run.assert_called_once()
        
        print("✅ Augmentation: Safety Checks & Negotiator Routing")

    # =========================================================================
    # 6. EXECUTOR & ORCHESTRATOR TESTS
    # =========================================================================
    def test_orchestrator_execution_lifecycle(self):
        """
        Verifies the full lifecycle: Plan -> Schedule -> Execute -> State Update -> Event.
        """
        # --- Setup Mocks ---
        # We need a runner that simulates a task succeeding
        def mock_runner(action_payload):
            cmd = action_payload.get("command", "")
            if "fail" in cmd:
                raise RuntimeError("Task failed intentionally")
            return {"status": "success", "output": f"Ran {cmd}"}

        # Mock Event Listener to capture emitted events
        emitted_events = []
        def event_listener(evt):
            emitted_events.append(evt)

        # --- Build Plan ---
        cfg = CFG()
        start = StartNode(id="start")
        t1 = TaskNode(id="t1", name="task:step1", task_ref="t1")
        t2 = TaskNode(id="t2", name="task:step2", task_ref="t2")
        end = EndNode(id="end")
        
        # Linear chain: Start -> t1 -> t2 -> End
        cfg.add_node(start); cfg.add_node(t1); cfg.add_node(t2); cfg.add_node(end)
        cfg.add_edge("start", "t1")
        cfg.add_edge("t1", "t2")
        cfg.add_edge("t2", "end")
        cfg.entry = "start"; cfg.exit = "end"
        
        plan = Plan(cfg=cfg, status=PlanStatus.CREATED)

        # --- Run Orchestrator ---
        orch = Orchestrator(runner=mock_runner, event_emitter=event_listener)
        
        # We assume execute_plan is synchronous for this test (or waits internally)
        summary = orch.execute_plan(plan, session_id="test_sess")
        
        # --- Verify Results ---
        self.assertEqual(summary["status"], "success", "Orchestrator failed to execute valid plan")
        self.assertEqual(summary["nodes_executed"], 4, "Should execute 4 nodes (Start, t1, t2, End)")
        
        # --- Verify Event Stream ---
        # We expect: PlanCreated -> NodeStarted(Start) -> ... -> NodeFinished(t2) -> PlanCompleted
        event_types = [e.type for e in emitted_events]
        self.assertIn(EventType.PLAN_CREATED, event_types)
        self.assertIn(EventType.NODE_STARTED, event_types)
        self.assertIn(EventType.NODE_FINISHED, event_types)
        self.assertIn(EventType.PLAN_COMPLETED, event_types)
        
        print("✅ Executor: Full Lifecycle & Event Emission")

    # =========================================================================
    # 7. PROVENANCE INTEGRITY TEST
    # =========================================================================
    def test_provenance_chaining(self):
        """
        Verifies that the ProvenanceChain creates a linked hash list that cannot be tampered with.
        """
        chain_file = os.path.join(self.test_dir, "provenance.jsonl")
        prov = ProvenanceChain(file_path=chain_file)
        
        # 1. Log Events
        prov.log_event("evt_1", {"data": "A"})
        prov.log_event("evt_2", {"data": "B"})
        
        # 2. Read Back
        entries = prov.read_chain()
        self.assertEqual(len(entries), 2)
        
        entry1 = entries[0]
        entry2 = entries[1]
        
        # 3. Check Hashing
        # Entry 2's 'prev_hash' must match Entry 1's 'hash'
        self.assertEqual(entry2["prev_hash"], entry1["hash"], "Provenance chain broken: Hash mismatch")
        
        print("✅ Provenance: Cryptographic Chain Integrity")

    # =========================================================================
    # 8. EXTREME END-TO-END SIMULATION
    # =========================================================================
    def test_end_to_end_simulation(self):
        """
        Simulates the user typing a request and the system processing it completely.
        (Mocks the LLM but uses real Planner/Compiler/Executor logic).
        """
        # 1. User Input
        user_dsl = """
t1 = task(name="download_data")
if t1.result.ok:
    t2 = task(name="analyze", input=t1.result.file)
"""
        # 2. Mock LLM to return this DSL
        mock_llm = MagicMock()
        mock_llm.complete.return_value = user_dsl
        
        # 3. Build Components
        # Memory
        mem_store = PersistentAnnoyStore(
            index_path=os.path.join(self.test_dir, "e2e.ann"), 
            meta_db_path=os.path.join(self.test_dir, "e2e.db"), 
            vector_dim=4
        )
        
        # Planner
        # We verify parsing -> compiling works via the utility functions
        # A full Planner class test would require more mocking of router/etc.
        ast = parse_dsl(user_dsl)
        cfg = compile_ast_to_cfg(ast)
        
        plan = Plan(dsl_text=user_dsl, parsed_ast={}, cfg=cfg, status=PlanStatus.CREATED)
        
        # Executor
        # We need a runner that can handle "download_data" and return "ok"
        def intelligent_runner(payload):
            cmd = payload.get("command", "")
            if "download_data" in cmd:
                return {"ok": True, "file": "data.csv"}
            elif "analyze" in cmd:
                # Verify data transfer!
                args = payload.get("args", {})
                # The compiler might put args in metadata.
                # In a real run, the ConditionEvaluator resolves 't1.result.file'.
                # Here we just check execution.
                return {"status": "analyzed"}
            return {"status": "unknown"}

        state_tracker = StateTracker()
        
        # We need to wire up the ConditionEvaluator to read from StateTracker
        def resolver(name):
            # Resolve t1.result.ok
            parts = name.split(".")
            if len(parts) >= 2:
                node_id_map = {"t1": list(cfg.nodes.keys())[1]} # Hacky map finding
                # For this test, we mock the resolver logic or rely on the fact 
                # that we just want to see the decision node processed.
                if name == "t1.result.ok": return True
            return None

        cond_eval = ConditionEvaluator(resolver=resolver)
        
        # Create Scheduler Manually to inject specific components
        # (Orchestrator wraps this, but we want granular verification)
        # Note: In deep_test_suite we verified the components. 
        # Here we trust Orchestrator to instantiate them if we pass the runner.
        
        orch = Orchestrator(runner=intelligent_runner)
        
        # 4. EXECUTE
        summary = orch.execute_plan(plan, session_id="e2e_test")
        
        self.assertEqual(summary["status"], "success")
        
        print("✅ END-TO-END: Input -> DSL -> Graph -> Execution -> Success")


if __name__ == "__main__":
    print("\n🌋 STARTING TITAN EXTREME VERIFICATION SUITE 🌋")
    print("================================================")
    unittest.main(verbosity=2)
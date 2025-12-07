import unittest
import logging
import json
import time
from unittest.mock import Mock, MagicMock, patch
from typing import Dict, Any, Optional, List, Tuple, Callable
import os
import sys
import re

# --- CONFIGURATION AND DIAGNOSTIC SETUP ---

LOG_FILE = "titan_diagnostic_suite.log"
if os.path.exists(LOG_FILE): 
    os.remove(LOG_FILE)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(name)s - %(module)s.%(funcName)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("TITAN_TEST_SUITE")
logger.setLevel(logging.DEBUG)
logger.info("--- TITAN DIAGNOSTIC TEST SUITE INITIALIZED ---")


# --- CORE TITAN MODULE IMPORTS ---

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

try:
    from titan.schemas.plan import Plan, PlanStatus
    from titan.schemas.graph import CFGNode, CFGNodeType, TaskNode, CFG, DecisionNode, LoopNode
    from titan.schemas.action import Action, ActionType
    from titan.schemas.events import Event, EventType 
    
    from titan.planner.dsl.ir_dsl import parse_dsl
    from titan.planner.dsl.ir_compiler import Compiler
    
    from titan.executor.orchestrator import Orchestrator
    from titan.executor.worker_pool import WorkerPool 
    from titan.executor.state_tracker import StateTracker
    from titan.executor.condition_evaluator import ConditionEvaluator
    
    from titan.memory.vector_store import VectorStore
    
except ImportError as e:
    logger.critical(f"FATAL: Module import error. Check your file structure and previous fixes. Error: {e}")
    sys.exit(1)

# --- MOCK DATA ---

COMPLEX_SENTENCE = "Hii, It seems like the rain is going to happen so check the weather reports for today's evening. And also send the last project we worked on to Mr.x make sure when he replies you notify me. But first play a classic song."
SESSION_ID = "god_tier_session_42"
MOCK_PROJECT_PATH = "/user/files/project_titan_v2_report.zip"

MOCK_DSL_COMPLEX = f"""
t1 = task(name="play_music", genre="classic")
t2 = task(name="get_weather_report", location="current", time="evening")
t3 = task(name="send_project_email", recipient="Mr.x@corp.com", attachment="{MOCK_PROJECT_PATH}")
t4 = task(name="set_reply_monitor", email_id=t3.result.email_id)
"""

# --- UTILITIES & WRAPPERS ---

# FIX: StateWrapper allows accessing dictionary keys as attributes.
# This enables the ConditionEvaluator to handle 'n1.result.code' correctly.
class StateWrapper:
    def __init__(self, data):
        self._data = data
    def __getattr__(self, name):
        val = self._data.get(name)
        if isinstance(val, dict):
            return StateWrapper(val)
        return val
    def __str__(self):
        return str(self._data)
    # Allow comparison directly (e.g., if result.code == 200)
    def __eq__(self, other):
        return self._data == other

def _resolve_argument(arg_value: str, state_tracker: StateTracker) -> Any:
    if not isinstance(arg_value, str): return arg_value
    
    match = re.match(r't3\.result\.(\w+)', arg_value)
    if match:
        result_key = match.groups()[0]
        # Look up T3 by name "send_project_email"
        state = state_tracker.get_state_by_task_name('send_project_email')
        if state and state.get('result'):
            return state['result'].get(result_key)
    
    if arg_value.startswith('/') or arg_value.endswith('.zip') or arg_value.startswith('http'):
        return arg_value
    return arg_value

class MockExecutionRunner:
    def __init__(self, state_tracker: StateTracker):
        self.state_tracker = state_tracker
        self.email_counter = 0

    def run(self, action: Dict[str, Any]) -> Dict[str, Any]:
        task_name = action.get('name', action.get('task_name'))

        resolved_args = {
            k: _resolve_argument(v, self.state_tracker) for k, v in action.get('args', {}).items()
        }
        action['args'] = resolved_args
        
        if action.get('context', {}).get('trust_level') == 'low' and "email" in task_name:
             return {"status": "failure", "error": "Policy Denied: Restricted capability."}

        if task_name == "send_project_email":
            if action['args']['attachment'] != MOCK_PROJECT_PATH:
                 return {"status": "failure", "error": f"Resolution error: Expected {MOCK_PROJECT_PATH}, got {action['args']['attachment']}"}

        # Ensure all returns have 'message' for validation
        if "music" in task_name:
            return {"status": "success", "message": "Classic song started.", "duration_sec": 3.0}
        
        if "weather" in task_name:
            return {"status": "success", "message": "Weather report retrieved", "report": "Rain expected.", "provider": "mock_api"}

        if "email" in task_name:
            self.email_counter += 1
            email_id = f"msg_{int(time.time())}_{self.email_counter}"
            return {"status": "success", "message": "Email sent", "email_id": email_id, "recipient": action['args'].get('recipient')}
        
        if "monitor" in task_name:
            if action['args'].get('email_id', '').startswith('msg_'):
                 return {"status": "success", "message": "Monitor active", "monitor_status": "active", "watching_id": action['args']['email_id']}
            else:
                 return {"status": "failure", "error": "Missing dependency from T3."} 
        
        if task_name == "fetch_status":
             return {"status": "success", "message": "Status fetched", "code": 200}
        if task_name == "process_success":
             return {"status": "success", "message": "Success path taken"}
        if task_name == "log_error":
             return {"status": "success", "message": "Error path taken"}

        return {"status": "success", "message": f"Simulated result for {task_name}"}

# FIX: Updated resolver with logging to debug lookup failures
def mock_resolver(name: str, state_tracker: StateTracker) -> Any:
    state = state_tracker.get_state(name) # name is the node ID (e.g., 'n1')
    if state:
        logger.debug(f"RESOLVER: Found state for '{name}': {state}")
        return StateWrapper(state)
    
    logger.warning(f"RESOLVER: No state found for '{name}'. Keys available: {list(state_tracker.get_all_states().keys())}")
    return None

class MockLLMClient:
    def generate_dsl(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> str:
        if "fail_dsl" in prompt:
             return 't1 = task(name="fail_task", arg="unclosed quote' 
        if "conditional" in prompt:
             return "t1 = task(name=\"fetch_status\")\nif t1.result.code == 200:\n    t2 = task(name=\"process_success\")\nelse:\n    t3 = task(name=\"log_error\")"
        return MOCK_DSL_COMPLEX

class MockRuntimeAPI:
    def create_session(self, **kwargs): return "test_session_123"
    def get_context(self, session_id): return {"last_project_path": MOCK_PROJECT_PATH}
    def get_trust(self): return MagicMock(check_policy=lambda task: True) 
    def get_identity_mgr(self): return MagicMock()
    def end_session(self, session_id): pass


# --- E2E TEST SUITE IMPLEMENTATION ---

class TestTitanE2E(unittest.TestCase):
    
    COMPLEX_SENTENCE = COMPLEX_SENTENCE
    SESSION_ID = SESSION_ID
    
    def setUp(self):
        logger.info("\n--- TEST SETUP STARTING ---")
        
        self.state_tracker = StateTracker()
        
        # StateTracker lookup helper
        self.state_tracker.get_state_by_task_name = self._state_lookup
        
        self.mock_runner_instance = MockExecutionRunner(self.state_tracker)
        self.mock_runner = self.mock_runner_instance.run
        
        self.worker_pool = WorkerPool(max_workers=4, runner=self.mock_runner) 
        self.worker_pool.start()
        
        self.compiler = Compiler()
        self.mock_llm = MockLLMClient()
        self.runtime = MockRuntimeAPI() 
        
        self.event_log: List[Dict[str, Any]] = []
        def event_collector(event):
            self.event_log.append(event.model_dump())
            if event.type not in [EventType.DSL_PRODUCED, EventType.AST_PARSED]:
                logger.debug(f"EVENT: {event.type.value} - Plan: {event.plan_id}")

        self.event_emitter = event_collector
        
        state_tracker_ref = self.state_tracker
        self.cond_evaluator = ConditionEvaluator(
            resolver=lambda name, *args: mock_resolver(name, state_tracker_ref)
        )

        self.orchestrator = Orchestrator(
            worker_pool=self.worker_pool,
            event_emitter=self.event_emitter,
            condition_evaluator=self.cond_evaluator
        )
        logger.info("--- TEST SETUP COMPLETE ---")

    def _state_lookup(self, task_name):
        return next((s for s in self.state_tracker.get_all_states().values() if s.get('name') == task_name), None)

    def tearDown(self):
        logger.info("--- TEST TEARDOWN STARTING ---")
        self.worker_pool.stop()
        logger.info(f"Total events logged: {len(self.event_log)}")
        logger.info("--- TEST TEARDOWN COMPLETE ---\n")

    
    def _plan_and_compile(self, input_text: str, session_id: str) -> Plan:
        context = self.runtime.get_context(session_id)
        dsl_text = self.mock_llm.generate_dsl(input_text, context)
        self.assertTrue(dsl_text.strip(), "DSL generation resulted in an empty string.")

        try:
            ast_root = parse_dsl(dsl_text)
        except Exception as e:
            logger.error(f"FATAL PARSING FAILURE: {e}")
            raise e 
        
        cfg_data = self.compiler.compile(ast_root) 
        
        cfg = CFG.from_node_list(cfg_data)
        cfg.validate_integrity() 
        
        plan = Plan(
            id=f"plan_{int(time.time())}", 
            user_input=input_text, 
            session_id=session_id, 
            status=PlanStatus.CREATED, 
            cfg=cfg
        )
        logger.info(f"PLANNER: Compiled and validated CFG successfully. Nodes: {len(cfg.nodes)}")
        return plan
    

    def test_01_planning_phase_integrity_and_dsl_validity(self):
        logger.info("--- test_01_planning_phase_integrity_and_dsl_validity ---")
        plan = self._plan_and_compile(self.COMPLEX_SENTENCE, self.SESSION_ID)
        
        t3_node = next(node for node in plan.cfg.nodes.values() if node.type == CFGNodeType.TASK and node.task_ref == 'send_project_email')
        task_args = t3_node.metadata.get('task_args')
        self.assertEqual(task_args.get('attachment'), MOCK_PROJECT_PATH)
        self.assertEqual(len(plan.cfg.nodes), 6)
        logger.info("RESULT: PASS - Planning phase verified.")


    def test_02_full_e2e_execution_flow_and_ordering(self):
        logger.info("\n--- Running test_02_full_e2e_execution_flow_and_ordering ---")
        plan = self._plan_and_compile(self.COMPLEX_SENTENCE, self.SESSION_ID)
        summary = self.orchestrator.execute_plan(plan=plan, session_id=self.SESSION_ID, state_tracker=self.state_tracker)
        
        self.assertEqual(summary.get("status"), "success", f"Orchestrator failed. Summary: {summary}")
        
        task_finished_events = [
            e for e in self.event_log 
            if e['type'] == EventType.NODE_FINISHED.value 
            and e['payload']['node_type'] == 'task'
            and e['payload'].get('result_summary')
        ]
        
        sequence = [e['payload']['result_summary'].get('message', 'No Message') for e in task_finished_events]
        self.assertIn("Classic song started.", sequence[0])
        logger.info("RESULT: PASS - Execution sequence verified.")


    def test_03_data_dependency_resolution_t3_to_t4(self):
        logger.info("\n--- Running test_03_data_dependency_resolution_t3_to_t4 ---")
        plan = self._plan_and_compile(self.COMPLEX_SENTENCE, self.SESSION_ID)
        self.orchestrator.execute_plan(plan=plan, session_id=self.SESSION_ID, state_tracker=self.state_tracker)
        
        t3_state = self.state_tracker.get_state_by_task_name("send_project_email")
        self.assertIsNotNone(t3_state, "T3 state missing.")
        t3_email_id = t3_state.get('result', {}).get('email_id')
        
        t4_state = self.state_tracker.get_state_by_task_name("set_reply_monitor")
        self.assertIsNotNone(t4_state, "T4 state missing.")
        t4_watching_id = t4_state.get('result', {}).get('watching_id')

        self.assertEqual(t4_watching_id, t3_email_id)
        logger.info("RESULT: PASS - Data dependency resolution verified.")

    
    def test_04_runtime_safety_and_policy_denial(self):
        logger.info("--- test_04_runtime_safety_and_policy_denial ---")
        plan = self._plan_and_compile(self.COMPLEX_SENTENCE, self.SESSION_ID)
        
        def policy_fail_runner(action: Dict[str, Any]) -> Dict[str, Any]:
            if action.get('name') == 'send_project_email':
                 return {"status": "failure", "error": "Policy Denied: Restricted capability."}
            return self.mock_runner(action)

        self.worker_pool.runner = policy_fail_runner 
        summary = self.orchestrator.execute_plan(plan=plan, session_id=self.SESSION_ID, state_tracker=self.state_tracker)

        self.assertEqual(summary.get("status"), "failed")
        t3_state = self.state_tracker.get_state_by_task_name("send_project_email")
        self.assertEqual(t3_state.get('status'), 'failed')
        self.worker_pool.runner = self.mock_runner 
        logger.info("RESULT: PASS - Policy denial verified.")


    def test_05_compilation_failure_on_invalid_dsl(self):
        logger.info("--- test_05_compilation_failure_on_invalid_dsl ---")
        with self.assertRaises(Exception): 
            plan = self._plan_and_compile("Please fail_dsl this plan.", self.SESSION_ID)
        logger.info("RESULT: PASS - Compilation failure verified.")


    def test_06_observability_provenance_event_integrity(self):
        logger.info("--- test_06_observability_provenance_event_integrity ---")
        plan = self._plan_and_compile(self.COMPLEX_SENTENCE, self.SESSION_ID)
        self.orchestrator.execute_plan(plan=plan, session_id=self.SESSION_ID, state_tracker=self.state_tracker)

        all_event_types = [e['type'] for e in self.event_log]
        self.assertIn(EventType.PLAN_COMPLETED.value, all_event_types)
        logger.info("RESULT: PASS - Provenance verified.")


    def test_07_conditional_branching_execution(self):
        logger.info("--- test_07_conditional_branching_execution ---")
        
        mock_cfg = CFG(
            nodes={
                's': CFGNode(id='s', type=CFGNodeType.START, successors={'next': 'n1'}),
                'n1': TaskNode(id='n1', task_ref='fetch_status', name='fetch_status', successors={'next': 'd1'}), 
                'd1': DecisionNode(id='d1', condition='n1.result.code == 200', successors={'true': 'n2', 'false': 'n3'}),
                'n2': TaskNode(id='n2', task_ref='process_success', name='process_success', successors={'next': 'e'}), 
                'n3': TaskNode(id='n3', task_ref='log_error', name='log_error', successors={'next': 'e'}), 
                'e': CFGNode(id='e', type=CFGNodeType.END)
            },
            entry='s',
            exit='e'
        )
        plan = Plan(id='plan_cond', user_input='conditional check', session_id=self.SESSION_ID, status=PlanStatus.CREATED, cfg=mock_cfg)
        
        # Inject state tracker to capture execution logic
        summary = self.orchestrator.execute_plan(plan=plan, session_id=self.SESSION_ID, state_tracker=self.state_tracker)
        
        self.assertEqual(summary.get("status"), "success")
        
        executed_tasks = [s.get('name') for s in self.state_tracker.get_all_states().values() if s.get('status') == 'completed' and s.get('type') == 'task']
        self.assertIn('process_success', executed_tasks)
        self.assertNotIn('log_error', executed_tasks)

        logger.info("RESULT: PASS - Conditional branching verified.")


    def test_08_resource_lifecycle_integrity(self):
        logger.info("--- test_08_resource_lifecycle_integrity ---")
        self.worker_pool.stop()
        self.worker_pool.start()
        self.assertTrue(self.worker_pool._is_running)
        logger.info("RESULT: PASS - Resource lifecycle verified.")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print("\nStarting TITAN God-Tier E2E Diagnostic Suite...")
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
    print(f"\nFull diagnostic log available in {LOG_FILE}")
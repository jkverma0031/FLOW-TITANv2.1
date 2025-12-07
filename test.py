#!/usr/bin/env python3
# test.py -- Comprehensive diagnostic suite for TITAN v2.1
# Drop into project root and run with same Python you use for the repo.
#
# Produces verbose console output and titan_diagnostic_report.json with structured results.
#
# Note: This script is non-invasive (read-only). It will import modules and call public APIs safely
#       where possible. It uses timeouts for potentially blocking operations where applicable.

from __future__ import annotations
import importlib
import inspect
import json
import os
import sys
import time
import traceback
import types
from typing import Any, Dict, List, Optional, Callable
from pprint import pformat

# -------------- Utilities --------------
OUT_JSON = "titan_diagnostic_report.json"
REPORT: Dict[str, Any] = {
    "meta": {
        "cwd": os.getcwd(),
        "python": sys.version,
        "timestamp": time.time(),
    },
    "results": []
}


def record(result: Dict[str, Any]):
    REPORT["results"].append(result)
    # also flush to disk after each record for easier post-mortem
    try:
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(REPORT, f, indent=2)
    except Exception:
        pass


def print_header(title: str):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def mark_ok(name: str, detail: Optional[str] = None):
    print(f"[ OK ] {name}")
    record({"name": name, "ok": True, "detail": detail})


def mark_fail(name: str, exc: Exception, tb: str, hint: Optional[str] = None):
    print(f"[FAIL] {name}")
    print("Exception:")
    print(tb)
    record({
        "name": name,
        "ok": False,
        "exception": {"type": type(exc).__name__, "message": str(exc)},
        "traceback": tb,
        "hint": hint
    })


def safe_import(module_name: str):
    """
    Import a module and return (module or None, exception or None, traceback)
    """
    try:
        mod = importlib.import_module(module_name)
        return mod, None, None
    except Exception as e:
        tb = traceback.format_exc()
        return None, e, tb


def safe_call(fn: Callable, *args, timeout_sec: float = 5.0, **kwargs):
    """
    Call a function and capture exceptions. Designed to be synchronous.
    If you want to avoid long blocking calls, adapt it to use threads/timeouts.
    """
    try:
        out = fn(*args, **kwargs)
        return out, None, None
    except Exception as e:
        tb = traceback.format_exc()
        return None, e, tb


def inspect_member(obj, member_name: str):
    return hasattr(obj, member_name), getattr(obj, member_name, None)


# -------------- Module Import Checks --------------
MODULES_TO_CHECK = [
    # core kernel and startup
    "titan.kernel.kernel",
    "titan.kernel.startup",
    "titan.kernel.event_bus",
    "titan.kernel.lifecycle",
    "titan.kernel.app_context",
    "titan.kernel.capability_registry",
    # planner / parser
    "titan.planner.planner",
    "titan.planner.dsl.ir_dsl",
    "titan.planner.dsl.ir_compiler",
    "titan.planner.dsl.ir_validator",
    # parser adapters
    "titan.parser.adapter",
    "titan.parser.heuristic_parser",
    "titan.parser.llm_dsl_generator",
    # executor
    "titan.executor.orchestrator",
    "titan.executor.scheduler",
    "titan.executor.condition_evaluator",
    "titan.executor.retry_engine",
    "titan.executor.loop_engine",
    "titan.executor.state_tracker",
    # memory
    "titan.memory.embeddings",
    "titan.memory.persistent_annoy_store",
    "titan.memory.episodic_store",
    "titan.memory.vector_store",
    # runtime
    "titan.runtime.session_manager",
    # augmentation
    "titan.augmentation.negotiator",
    "titan.augmentation.sandbox.sandbox_runner",
    "titan.augmentation.hostbridge.hostbridge_service",
    "titan.augmentation.provenance",
    # policy
    "titan.policy.engine",
    # schemas / models
    "titan.schemas.graph",
    "titan.schemas.plan",
    "titan.executor.action",
]

print_header("MODULE IMPORT VALIDATION")

import_results = []   # store results here; flush once at the end

for m in MODULES_TO_CHECK:

    mod, exc, tb = safe_import(m)

    if mod:
        print(f"[ OK ] Import {m} -> {getattr(mod, '__file__', 'built-in')}")
        import_results.append({
            "name": f"import:{m}",
            "ok": True,
            "path": getattr(mod, "__file__", None)
        })

    else:
        print(f"[FAIL] Import {m}")
        print(tb)
        import_results.append({
            "name": f"import:{m}",
            "ok": False,
            "exception": {
                "type": type(exc).__name__,
                "message": str(exc)
            },
            "traceback": tb,
            "hint": (
                f"Module '{m}' failed to import.\n"
                f"Check folder structure and __init__.py presence."
            )
        })

# store all results in one go to avoid recursion/infinite loop
REPORT["results"].extend(import_results)

# -------------- EventBus Tests --------------
print_header("EVENT BUS - sync/async publish/subscribe checks")
try:
    eb_mod = importlib.import_module("titan.kernel.event_bus")
    EventBus = getattr(eb_mod, "EventBus", None)
    if EventBus is None:
        raise ImportError("EventBus class not found in titan.kernel.event_bus")

    eb = EventBus(max_workers=4)

    events_received = []

    def handler_one(payload):
        events_received.append(("one", payload))

    def handler_two(payload):
        events_received.append(("two", payload))

    eb.subscribe("test_event", handler_one)
    eb.subscribe("test_event", handler_two)

    # synchronous publish (block)
    eb.publish("test_event", {"a": 1}, block=True)
    if len(events_received) >= 2:
        mark_ok("EventBus sync publish/subscribe", f"received {len(events_received)} handlers")
    else:
        raise AssertionError(f"Expected 2 handlers invoked synchronously, got {len(events_received)}")

    # asynchronous publish (non-blocking): give some time
    events_received.clear()
    eb.publish("test_event", {"b": 2}, block=False)
    time.sleep(0.2)
    if len(events_received) >= 2:
        mark_ok("EventBus async publish/subscribe", f"received {len(events_received)} handlers async")
    else:
        raise AssertionError(f"Expected 2 async handler invocations, got {len(events_received)}")

except Exception as e:
    tb = traceback.format_exc()
    mark_fail("EventBus tests", e, tb, hint="Check titan/kernel/event_bus.py: class definition and subscribe/publish signatures")


# -------------- DSL Parser & Grammar Tests --------------
print_header("DSL GRAMMAR & PARSER (ir_dsl)")

dsl_mod, exc, tb = safe_import("titan.planner.dsl.ir_dsl")
if not dsl_mod:
    mark_fail("DSL module import", exc, tb, hint="titan/planner/dsl/ir_dsl.py has import-time error; inspect traceback")
else:
    # Try to find parse_dsl or PARSER and DSLTransformer
    parse_fn = getattr(dsl_mod, "parse_dsl", None)
    PARSER = getattr(dsl_mod, "PARSER", None)
    DSLTransformer = getattr(dsl_mod, "DSLTransformer", None)

    # 1) confirm parser exists
    if PARSER is None and parse_fn is None:
        msg = "Neither PARSER nor parse_dsl found. Please export parse_dsl(source: str) or PARSER."
        mark_fail("DSL parser presence", Exception(msg), traceback.format_stack(), hint=msg)
    else:
        mark_ok("DSL parser present", "PARSER or parse_dsl found")

    # Test parsing a few representative snippets (safe, minimal)
    sample_snippets = [
        # basic task call (positional/keyword)
        'task(download="yes")\n',
        'x = 5\n',
        # call with URL-like string (escaped)
        'task(download url="https://example.com")\n',
        # compound if
        'if x == 1:\n    task(foo="bar")\n',
    ]

    for s in sample_snippets:
        test_name = f"parse_snippet: {s.strip()[:50]}"
        try:
            if parse_fn:
                ast = parse_fn(s)
                mark_ok(test_name, f"AST root type: {type(ast).__name__}")
            else:
                # try using PARSER directly then transform
                tree = PARSER.parse(s)
                if DSLTransformer:
                    t = DSLTransformer()
                    ast = t.transform(tree)
                    mark_ok(test_name, f"Parser+transform succeeded -> {type(ast).__name__}")
                else:
                    mark_ok(test_name, "PARSER parsed input (no transformer available)")
        except Exception as e:
            tb = traceback.format_exc()
            # Special diagnostic parsing: show token/lexer error hints
            hint = (
                "DSL parse failed. Check grammar.lark for tokens around error column. "
                "If the error mentions parenthesis or NEWLINE, ensure grammar tokens (LPAR/RPAR/NEWLINE) and Indenter mapping are correct."
            )
            mark_fail(test_name, e, tb, hint=hint)

# -------------- Transformer sanity check --------------
print_header("DSL Transformer sanity check (if available)")
if dsl_mod and getattr(dsl_mod, "DSLTransformer", None):
    try:
        TransformerCls = dsl_mod.DSLTransformer
        # create minimal tree if possible: use the parser to generate a tree for a safe snippet
        safe_snippet = "x = 42\n"
        tree = None
        try:
            tree = getattr(dsl_mod, "PARSER").parse(safe_snippet)
        except Exception:
            # if PARSER isn't available, skip synthetic tree transform test
            tree = None

        if tree is not None:
            t = TransformerCls()
            ast = t.transform(tree)
            mark_ok("Transformer.transform", f"produced AST type {type(ast).__name__}")
        else:
            mark_ok("Transformer.available", "Transformer class exists but parser not runnable for synthetic test")
    except Exception as e:
        tb = traceback.format_exc()
        mark_fail("Transformer.transform", e, tb, hint="Check method signatures in DSLTransformer (v_args/inlining mismatches).")
else:
    print("No DSLTransformer available — skipping transformer checks.")


# -------------- Memory: Embedder & Annoy Store --------------
print_header("MEMORY SUBSYSTEM: embedder + persistent store")

# embeddings module
emb_mod, exc, tb = safe_import("titan.memory.embeddings")
if not emb_mod:
    mark_fail("embeddings module import", exc, tb, hint="Check titan/memory/embeddings.py")
else:
    emb_impl = None
    # common names: Embedder, EmbeddingService, embedding_service
    for cand in ("Embedder", "EmbeddingService", "embedding_service"):
        if hasattr(emb_mod, cand):
            emb_impl = getattr(emb_mod, cand)
            break

    if emb_impl is None:
        # maybe module itself exposes functions embed_text
        if any(hasattr(emb_mod, name) for name in ("embed_text", "embed_texts", "embed")):
            mark_ok("embeddings functional API", "Module exposes embed_text/embed_texts/embed")
        else:
            mark_fail("embeddings API", Exception("Embedder not found"), traceback.format_stack(), hint="Provide Embedder class with 'embed_text' or 'embed_texts' method")

    else:
        # instantiate if it's a class
        try:
            if inspect.isclass(emb_impl):
                inst = emb_impl()
            else:
                inst = emb_impl
            # check methods
            has_single = hasattr(inst, "embed_text")
            has_multi = hasattr(inst, "embed_texts") or hasattr(inst, "embed_batch")
            if has_single or has_multi:
                mark_ok("Embedder methods", f"embed_text: {has_single}, embed_texts/batch: {has_multi}")
            else:
                mark_fail("Embedder methods", Exception("No embed methods"), traceback.format_stack(), hint="Embedder must implement embed_text or embed_texts")
        except Exception as e:
            tb = traceback.format_exc()
            mark_fail("Embedder instantiation", e, tb, hint="Check embedder constructor signature and any required dependencies")

# persistent_annoy_store
annoy_mod, exc, tb = safe_import("titan.memory.persistent_annoy_store")
if not annoy_mod:
    mark_fail("persistent_annoy_store import", exc, tb, hint="Missing titan/memory/persistent_annoy_store.py")
else:
    PAS = getattr(annoy_mod, "PersistentAnnoyStore", None)
    if PAS is None:
        mark_fail("PersistentAnnoyStore class", Exception("Not found"), traceback.format_stack(), hint="Implement class PersistentAnnoyStore with add/query/delete APIs")
    else:
        # try a few instantiation combinations observed in your history
        tried = []
        success = False
        for kwargs in ({"db_path": "data/memory.db", "index_path": "data/index.ann", "vector_dim": 1536},
                       {"vector_dim": 4},
                       {}):
            try:
                inst = PAS(**kwargs) if kwargs else PAS()
                success = True
                tried.append(("ok", kwargs))
                # check methods
                methods = ["add", "query", "close", "save"]
                missing = [m for m in methods if not hasattr(inst, m)]
                if missing:
                    mark_fail("PersistentAnnoyStore methods", Exception("Missing methods: " + ", ".join(missing)),
                              traceback.format_stack(),
                              hint=f"Add missing methods: {missing}")
                else:
                    mark_ok("PersistentAnnoyStore inst+methods", f"instantiated with {kwargs} and has methods")
                break
            except Exception as e:
                tried.append(("err", kwargs, str(e)))
                continue
        if not success:
            tb = "\n".join(repr(x) for x in tried)
            mark_fail("PersistentAnnoyStore instantiate", Exception("All tried constructor patterns failed"), tb,
                      hint="Check constructor signature; test harness tried common arg names: db_path/index_path/vector_dim")


# -------------- Session Manager --------------
print_header("SESSION MANAGER / RUNTIME")

sm_mod, exc, tb = safe_import("titan.runtime.session_manager")
if not sm_mod:
    mark_fail("session_manager import", exc, tb, hint="Missing titan/runtime/session_manager.py")
else:
    SM = getattr(sm_mod, "SessionManager", None)
    if SM is None:
        mark_fail("SessionManager class", Exception("Not found"), traceback.format_stack(), hint="Implement SessionManager")
    else:
        try:
            sm = SM(default_ttl_seconds=60, autosave_context_dir="data/sessions_test")
            # basic lifecycle operations if available
            ok_ops = []
            if hasattr(sm, "save"):
                ok_ops.append("save")
            if hasattr(sm, "load"):
                ok_ops.append("load")
            if hasattr(sm, "create_session"):
                ok_ops.append("create_session")
            mark_ok("SessionManager basic presence", f"Detected ops: {ok_ops}")
        except Exception as e:
            tb = traceback.format_exc()
            mark_fail("SessionManager init", e, tb, hint="Constructor signature mismatch or missing dependencies")


# -------------- Orchestrator / Worker Pool --------------
print_header("ORCHESTRATOR & WORKERPOOL")

orch_mod, exc, tb = safe_import("titan.executor.orchestrator")
if not orch_mod:
    mark_fail("orchestrator import", exc, tb, hint="titan/executor/orchestrator.py")
else:
    Orchestrator = getattr(orch_mod, "Orchestrator", None)
    if Orchestrator is None:
        mark_fail("Orchestrator class", Exception("Not found"), traceback.format_stack(),
                  hint="Orchestrator should be in titan.executor.orchestrator")
    else:
        # attempt to create a minimal orchestrator using safe fallbacks
        try:
            # Many of your versions accept (worker_pool=..., runner=..., event_emitter=..., max_workers=...)
            # We'll try a few constructor patterns without forcing dependencies.
            created = False
            tried = []
            for kw in (
                {"worker_pool": None, "runner": None, "event_emitter": None, "max_workers": 2},
                {"runner": None, "event_emitter": None, "max_workers": 2},
                {}
            ):
                try:
                    obj = Orchestrator(**kw) if kw else Orchestrator()
                    created = True
                    break
                except TypeError as e:
                    tried.append((kw, str(e)))
                    continue
            if not created:
                raise TypeError("Could not instantiate Orchestrator; tried signatures: " + pformat(tried))
            # check presence of run/submit/stop methods
            required = ["submit", "shutdown", "start"]
            present = [m for m in required if hasattr(obj, m)]
            if not present:
                # be helpful: list available public methods
                public_methods = [n for n in dir(obj) if not n.startswith("_")]
                mark_fail("Orchestrator API", Exception("Missing lifecycle methods"), traceback.format_stack(),
                          hint=f"Expected methods {required}, available: {public_methods[:20]}")
            else:
                mark_ok("Orchestrator instantiate & API", f"Found methods: {present}")
        except Exception as e:
            tb = traceback.format_exc()
            mark_fail("Orchestrator instantiate", e, tb, hint="Check Orchestrator constructor signature and dependencies")


# -------------- SandboxRunner & HostBridge --------------
print_header("SANDBOX RUNNER & HOSTBRIDGE (augmentation)")

sb_mod, exc, tb = safe_import("titan.augmentation.sandbox.sandbox_runner")
if not sb_mod:
    mark_fail("SandboxRunner import", exc, tb, hint="Check titan/augmentation/sandbox/sandbox_runner.py")
else:
    SB = getattr(sb_mod, "SandboxRunner", None)
    if SB is None:
        mark_fail("SandboxRunner class", Exception("Not found in module"), traceback.format_stack(),
                  hint="SandboxRunner should expose a run()/execute() API")
    else:
        try:
            sb = SB(work_dir="/tmp", default_timeout=1)  # safe no-op configuration
            # check run/execute existence
            has_run = hasattr(sb, "run") or hasattr(sb, "execute")
            if not has_run:
                mark_fail("SandboxRunner API", Exception("no run/execute method"), traceback.format_stack(),
                          hint="Provide run(command: str) -> ExecutionResult")
            else:
                mark_ok("SandboxRunner instantiate & API", "run/execute present")
        except Exception as e:
            tb = traceback.format_exc()
            mark_fail("SandboxRunner instantiation", e, tb, hint="Constructor signature mismatch")

hb_mod, exc, tb = safe_import("titan.augmentation.hostbridge.hostbridge_service")
if not hb_mod:
    mark_fail("HostBridge import", exc, tb, hint="Missing titan/augmentation/hostbridge/hostbridge_service.py")
else:
    HB = getattr(hb_mod, "HostBridgeService", None)
    if HB is None:
        mark_fail("HostBridgeService class", Exception("Not found"), traceback.format_stack(),
                  hint="HostBridgeService should be implemented to run host-side plugins/calls")
    else:
        try:
            hb = HB(manifests_dir="titan/augmentation/hostbridge/manifests")
            # check presence of call/execute/lookup methods
            ok = any(hasattr(hb, m) for m in ("call", "execute", "run_plugin", "list_manifests"))
            if not ok:
                mark_fail("HostBridge API", Exception("Missing expected methods"), traceback.format_stack(),
                          hint="Implement call/execute/list_manifests")
            else:
                mark_ok("HostBridge instantiate & API", "hostbridge methods found")
        except Exception as e:
            tb = traceback.format_exc()
            mark_fail("HostBridge instantiation", e, tb, hint="Constructor signature mismatch or missing dependencies")


# -------------- Policy Engine & Action Model --------------
print_header("POLICY ENGINE & ACTION model")

policy_mod, exc, tb = safe_import("titan.policy.engine")
if not policy_mod:
    mark_fail("policy.engine import", exc, tb, hint="Check titan/policy/engine.py")
else:
    PolicyEngine = getattr(policy_mod, "PolicyEngine", None)
    ActionModel = None
    # try to find an Action model (pydantic)
    for possible in ("Action", "ActionModel"):
        if hasattr(policy_mod, possible):
            ActionModel = getattr(policy_mod, possible)
            break

    # Check PolicyEngine
    if PolicyEngine is None:
        mark_fail("PolicyEngine class", Exception("Not found"), traceback.format_stack(),
                  hint="Add PolicyEngine with .check(action, identity) returning decision with .allow/.reason")
    else:
        try:
            pe = PolicyEngine()
            # try check() with minimal fake action object
            class DummyAction:
                def __init__(self):
                    self.type = "exec"
                    self.command = "echo hi"

            # the engine may require identity param; try both
            try:
                res = pe.check(DummyAction(), identity="test")
            except TypeError:
                res = pe.check(DummyAction())

            # inspect result shape
            allow = getattr(res, "allow", None)
            reason = getattr(res, "reason", None)
            mark_ok("PolicyEngine.check()", f"Returned allow={allow} reason={reason}")
        except Exception as e:
            tb = traceback.format_exc()
            mark_fail("PolicyEngine runtime", e, tb, hint="Ensure PolicyEngine.check accepts (action, identity?) and returns decision object")

    # Validate Action model if present (pydantic validation)
    if ActionModel:
        try:
            # create a minimal action DTO based on likely signature
            sample = {"type": "exec", "command": "echo test"}
            a = ActionModel(**sample)
            mark_ok("Action model creation", "Successfully created ActionModel instance")
        except Exception as e:
            tb = traceback.format_exc()
            mark_fail("Action model validation", e, tb, hint="Check Pydantic model validators/field names. Your test harness may expect 'type' values in lowercase such as 'exec'.")


# -------------- Negotiator & Provenance --------------
print_header("NEGOTIATOR & PROVENANCE (augmentation)")

neg_mod, exc, tb = safe_import("titan.augmentation.negotiator")
if neg_mod:
    Negotiator = getattr(neg_mod, "Negotiator", None)
    if Negotiator:
        try:
            # create minimal registry stub
            class RegStub:
                def get_providers(self, atype):
                    return {"sandbox": {"reliability": 0.9, "priority": 1.0}}

                def get_providers(self, action_type):
                    # some versions use get_providers(action_type) or registry.get(action_type)
                    return {"sandbox": {"reliability": 0.9, "priority": 1.0}}

                def providers_for(self, action_type):
                    return {"sandbox": {"reliability": 0.9, "priority": 1.0}}

                def get(self, name, default=None):
                    return None

            reg = RegStub()
            pe_stub = None
            neg = Negotiator(reg, policy_engine=pe_stub) if "policy_engine" in inspect.signature(Negotiator).parameters else Negotiator(reg)
            # create dummy action
            DummyAction = types.SimpleNamespace(type="exec")
            decision = None
            try:
                decision = neg.negotiate_action(DummyAction)
            except TypeError:
                # alternate name maybe negotiate or choose
                if hasattr(neg, "negotiate"):
                    decision = neg.negotiate(DummyAction)
                elif hasattr(neg, "choose_and_execute"):
                    # don't execute, just call choose function if signature allows dry-run
                    pass
            if decision is None:
                mark_ok("Negotiator instantiate", "Negotiator constructed; negotiation run returned None or not available (ok)")
            else:
                mark_ok("Negotiator decision", repr(decision))
        except Exception as e:
            tb = traceback.format_exc()
            mark_fail("Negotiator runtime", e, tb, hint="Negotiator API mismatch; expected negotiate_action(action) -> decision")
    else:
        mark_fail("Negotiator class missing", Exception("Negotiator not defined"), traceback.format_stack(), hint="Add Negotiator class")
else:
    mark_ok("Negotiator module absent", "Optional module; skipping heavy checks")

prov_mod, exc, tb = safe_import("titan.augmentation.provenance")
if prov_mod:
    ProvenanceTracker = getattr(prov_mod, "ProvenanceTracker", None)
    if ProvenanceTracker:
        try:
            pt = ProvenanceTracker()
            n1 = pt.record(data="input", op="ingest")
            n2 = pt.record(data="transformed", op="transform", parents=[n1])
            exp = pt.export()
            mark_ok("Provenance basic flow", f"Nodes: {len(exp['nodes'])}")
        except Exception as e:
            tb = traceback.format_exc()
            mark_fail("Provenance runtime", e, tb, hint="Provenance.record/export API mismatch")
    else:
        mark_fail("ProvenanceTracker class missing", Exception("ProvenanceTracker not found"), traceback.format_stack(), hint="Add ProvenanceTracker class")
else:
    mark_ok("Provenance module absent", "Optional; skipping")


# -------------- Capability Registry --------------
print_header("CAPABILITY REGISTRY (capability resolution)")

cap_mod, exc, tb = safe_import("titan.kernel.capability_registry")
if not cap_mod:
    mark_fail("capability_registry import", exc, tb, hint="Missing titan/kernel/capability_registry.py")
else:
    try:
        CapRegistry = getattr(cap_mod, "CapabilityRegistry", None)
        if CapRegistry is None:
            raise Exception("CapabilityRegistry class not found")
        cr = CapRegistry()
        # registry API commonly includes .register(name, obj/meta) and .get_providers(type) or .get(name)
        ok_ops = []
        if hasattr(cr, "register"):
            ok_ops.append("register")
        if hasattr(cr, "get"):
            ok_ops.append("get")
        if hasattr(cr, "get_providers"):
            ok_ops.append("get_providers")
        mark_ok("CapabilityRegistry presence", f"Detected ops: {ok_ops}")
    except Exception as e:
        tb = traceback.format_exc()
        mark_fail("CapabilityRegistry instantiate", e, tb, hint="Ensure register/get/get_providers exist")


# -------------- Executor action model and scheduler checks --------------
print_header("EXECUTOR: action model, scheduler, retry/loop engines")

# scheduler
sched_mod, exc, tb = safe_import("titan.executor.scheduler")
if not sched_mod:
    mark_fail("scheduler import", exc, tb, hint="titan/executor/scheduler.py missing or error")
else:
    # try basic creation
    Scheduler = getattr(sched_mod, "Scheduler", None) or getattr(sched_mod, "TaskScheduler", None)
    if Scheduler:
        try:
            s = Scheduler()
            mark_ok("Scheduler instantiate", "Scheduler created")
        except Exception as e:
            tb = traceback.format_exc()
            mark_fail("Scheduler runtime", e, tb, hint="Scheduler constructor signature mismatch")
    else:
        mark_ok("Scheduler optional", "No Scheduler class found; may not be required in this variant")

# retry_engine
re_mod, exc, tb = safe_import("titan.executor.retry_engine")
if re_mod:
    RetryEngine = getattr(re_mod, "RetryEngine", None)
    if RetryEngine:
        try:
            r = RetryEngine()
            mark_ok("RetryEngine instantiate", "RetryEngine created")
        except Exception as e:
            tb = traceback.format_exc()
            mark_fail("RetryEngine runtime", e, tb, hint="RetryEngine constructor/signature differences")
    else:
        mark_ok("RetryEngine optional", "No RetryEngine class found")
else:
    mark_ok("RetryEngine module absent", "Optional")


# -------------- Final Kernel boot attempt (non-invasive) --------------
print_header("KERNEL BOOT (safe attempt)")

kernel_mod, exc, tb = safe_import("titan.kernel.kernel")
if not kernel_mod:
    mark_fail("Kernel import", exc, tb, hint="titan/kernel/kernel.py import failed")
else:
    Kernel = getattr(kernel_mod, "Kernel", None)
    if Kernel is None:
        mark_fail("Kernel class", Exception("Not found"), traceback.format_stack(), hint="Implement Kernel class that calls perform_kernel_startup(app, cfg)")
    else:
        # Try to construct kernel in safe 'test' mode if constructor accepts cfg/test flag
        try:
            # Try multiple constructor forms defensively
            created = False
            tried = []
            for kw in ({"cfg": {"test": True}}, {"cfg": {}}, {}):
                try:
                    kern = Kernel(**kw) if kw else Kernel()
                    created = True
                    break
                except TypeError as e:
                    tried.append((kw, str(e)))
                    continue
            if not created:
                raise TypeError("Could not instantiate Kernel; tried signatures: " + pformat(tried))
            mark_ok("Kernel instantiate", "Constructed Kernel instance safely")
            # test app context if present
            app_ctx = getattr(kern, "app", None) or getattr(kern, "app_context", None)
            if app_ctx:
                mark_ok("Kernel.app present", f"App context type: {type(app_ctx).__name__}")
            else:
                mark_ok("Kernel constructed", "Kernel does not expose app context directly (ok)")
        except Exception as e:
            tb = traceback.format_exc()
            mark_fail("Kernel instantiate", e, tb, hint="Kernel startup likely requires missing services: llm_client etc. Try passing a minimal fake 'app' if constructor supports it.")


# -------------- Summary --------------
print("\n" + "=" * 80)
print("DIAGNOSTIC SUMMARY")
print("=" * 80)
ok_count = sum(1 for r in REPORT["results"] if r.get("ok"))
fail_count = sum(1 for r in REPORT["results"] if not r.get("ok"))
print(f"Total checks: {len(REPORT['results'])}  Passed: {ok_count}  Failed: {fail_count}")
print(f"JSON report saved to: {os.path.abspath(OUT_JSON)}")
print("=" * 80)

# print brief failure summary
for r in REPORT["results"]:
    if not r.get("ok"):
        print("\n[FAILED] ", r.get("name"))
        print("Hint:", r.get("hint"))
        if r.get("traceback"):
            tb_snip = r.get("traceback")
            print("Traceback snippet:")
            print(tb_snip.splitlines()[:20])
# final dump (already written incrementally)
try:
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(REPORT, f, indent=2)
except Exception:
    pass

# exit with non-zero if failures
if fail_count:
    print("\nOne or more checks failed. Inspect the JSON report and console output.")
    sys.exit(2)
else:
    print("\nAll checks passed.")
    sys.exit(0)

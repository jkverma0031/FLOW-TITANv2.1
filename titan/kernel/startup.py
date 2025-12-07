# Path: titan/kernel/startup.py
from __future__ import annotations
import logging
from typing import Optional

from titan.augmentation.sandbox.sandbox_runner import SandboxRunner
from titan.augmentation.sandbox.docker_adapter import DockerAdapter
from titan.augmentation.sandbox.cleanup import cleanup_orphaned_containers

from titan.augmentation.hostbridge.hostbridge_service import HostBridgeService
# Negotiator import will be attempted dynamically below

from titan.augmentation.safety import is_command_safe

from titan.memory.persistent_annoy_store import PersistentAnnoyStore
from titan.memory.episodic_store import EpisodicStore
from titan.memory.embeddings import Embedder

from titan.runtime.session_manager import SessionManager

from titan.executor.orchestrator import Orchestrator
from titan.executor.worker_pool import WorkerPool

from titan.kernel.event_bus import EventBus
from titan.kernel.capability_registry import CapabilityRegistry

from titan.parser.adapter import ParserAdapter
from titan.parser.heuristic_parser import HeuristicParser
from titan.parser.llm_dsl_generator import LLMDslGenerator


logger = logging.getLogger(__name__)


def _import_negotiator():
    """Attempt to import Negotiator in multiple common names to avoid hard import failures."""
    try:
        # Try the canonical symbol first
        from titan.augmentation.negotiator import Negotiator
        return Negotiator
    except Exception:
        pass
    try:
        import titan.augmentation.negotiator as _mod
        # common alternative class names
        for attr in ("Negotiator", "NegotiatorService", "NegotiatorEngine"):
            if hasattr(_mod, attr):
                return getattr(_mod, attr)
    except Exception:
        pass
    logger.warning("Negotiator class not found in titan.augmentation.negotiator; startup will continue without a negotiator instance")
    return None


def perform_kernel_startup(app, cfg: Optional[dict] = None):
    """
    Build and register subsystems into AppContext.
    cfg (optional) may contain tuning values:
      - worker_pool_max_workers: int
      - memory_vector_dim: int
      - memory_db_path, index_path
      - session_autosave_dir
    """
    cfg = cfg or {}
    # 1. Event Bus
    event_bus = EventBus()
    app.register("event_bus", event_bus)

    heuristic = HeuristicParser()
    # allow llm_client to be passed via app; llm_client may be None in tests
    llm_client = app.get("llm_client", None) if hasattr(app, "get") else None
    dsl_gen = LLMDslGenerator(llm_client=llm_client)
    adapter = ParserAdapter(heuristic_parser=heuristic, llm_dsl_generator=dsl_gen)

    app.register("parser_adapter", adapter)


    # 2. Memory
    vec_db = cfg.get("memory_db_path", "data/memory.db")
    idx_path = cfg.get("memory_index_path", "data/index.ann")
    vec_dim = cfg.get("memory_vector_dim", 1536)
    vec_store = PersistentAnnoyStore(db_path=vec_db, index_path=idx_path, vector_dim=vec_dim)
    epi_path = cfg.get("episodic_path", "data/episodic.jsonl")
    epi_store = EpisodicStore(epi_path)

    embedder = Embedder()
    app.register("vector_store", vec_store)
    app.register("episodic_store", epi_store)
    app.register("embedding_service", embedder)

    # 3. Runtime (persistent sessions)
    session_dir = cfg.get("session_autosave_dir", "data/sessions")
    session_mgr = SessionManager(default_ttl_seconds=cfg.get("session_ttl", 3600), autosave_context_dir=session_dir)
    app.register("session_manager", session_mgr)

    # 4. Sandbox & Docker + cleanup
    sandbox = SandboxRunner(work_dir=cfg.get("sandbox_work_dir", "/tmp/titan_sandbox"), default_timeout=cfg.get("sandbox_timeout", 30))
    docker = DockerAdapter(image=cfg.get("docker_image", "python:3.11-slim"), work_dir=cfg.get("docker_work_dir", "/work"), timeout=cfg.get("docker_timeout", 60))
    app.register("sandbox", sandbox)
    app.register("docker", docker)
    # register cleaning function so lifecycle can call it
    app.register("sandbox_cleanup", cleanup_orphaned_containers)

    # 5. HostBridge
    hb = HostBridgeService(manifests_dir=cfg.get("hostbridge_manifests_dir", "titan/augmentation/hostbridge/manifests"))
    app.register("hostbridge", hb)

    # 6. Capability Registry
    caps = CapabilityRegistry()
    caps.register("sandbox", sandbox)
    caps.register("docker", docker)
    caps.register("hostbridge", hb)
    app.register("cap_registry", caps)

    # 7. Negotiator (imported safely)
    NegotiatorClass = _import_negotiator()
    negotiator = None
    if NegotiatorClass:
        try:
            negotiator = NegotiatorClass(
                hostbridge_service=hb,
                sandbox_runner=sandbox,
                plugin_runner=None,
                trust_check_fn=None,
                policy_check_fn=None,
            )
        except Exception:
            logger.exception("Failed to instantiate Negotiator; continuing without one")
            negotiator = None
    else:
        logger.info("Negotiator unavailable; registering None (some capabilities may be limited)")

    app.register("negotiator", negotiator)

    # 8. WorkerPool (explicitly created so we can configure threads)
    max_workers = cfg.get("worker_pool_max_workers", 8)
    worker_pool = WorkerPool(max_workers=max_workers, runner=None)  # runner is configured in Orchestrator/Negotiator
    app.register("worker_pool", worker_pool)

    # 9. Executor Orchestrator
    orch = None
    try:
        orch = Orchestrator(worker_pool=worker_pool, runner=(negotiator.choose_and_execute if negotiator else None), event_emitter=event_bus.publish, max_workers=max_workers)
    except TypeError:
        orch = Orchestrator(runner=(negotiator.choose_and_execute if negotiator else None), event_emitter=event_bus.publish, max_workers=max_workers)
    app.register("orchestrator", orch)

    logger.info("[Kernel] Startup wiring completed.")

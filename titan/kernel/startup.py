# titan/kernel/startup.py
from __future__ import annotations
import logging
from typing import Optional

# Sandbox & HostBridge
from titan.augmentation.sandbox.sandbox_runner import SandboxRunner
from titan.augmentation.sandbox.docker_adapter import DockerAdapter
from titan.augmentation.sandbox.execution_adapter import LocalExecutionAdapter
from titan.augmentation.sandbox.cleanup import cleanup_orphaned_containers

from titan.augmentation.hostbridge.hostbridge_service import HostBridgeService
from titan.augmentation.safety import is_command_safe

# Memory subsystem
from titan.memory.persistent_annoy_store import PersistentAnnoyStore
from titan.memory.episodic_store import EpisodicStore
from titan.memory.embeddings import Embedder

# Runtime
from titan.runtime.session_manager import SessionManager
from titan.runtime.context_store import ContextStore
from titan.runtime.trust_manager import TrustManager
from titan.runtime.identity import IdentityManager

# Executor
from titan.executor.orchestrator import Orchestrator
from titan.executor.worker_pool import WorkerPool

# Kernel
from titan.kernel.event_bus import EventBus
from titan.kernel.capability_registry import CapabilityRegistry
from titan.kernel.app_context import _SENTINEL

# Parser
from titan.parser.adapter import ParserAdapter
from titan.parser.heuristic_parser import HeuristicParser
from titan.parser.llm_dsl_generator import LLMDslGenerator

# Plugins
from titan.runtime.plugins.registry import register_plugin
from titan.runtime.plugins.filesystem import FilesystemPlugin
from titan.runtime.plugins.http import HTTPPlugin

# LLM Provider System
from titan.models.provider import ProviderRouter
from titan.models.groq_provider import GroqProvider

# Policy
try:
    from titan.policy.engine import PolicyEngine
    _policy_engine = PolicyEngine()
except Exception:
    _policy_engine = None

logger = logging.getLogger(__name__)


def _import_negotiator():
    """
    Safely import Negotiator, fallback to alternative names.
    """
    try:
        from titan.augmentation.negotiator import Negotiator
        return Negotiator
    except Exception:
        pass

    try:
        import titan.augmentation.negotiator as _mod
        for attr in ("Negotiator", "NegotiatorService", "NegotiatorEngine"):
            if hasattr(_mod, attr):
                return getattr(_mod, attr)
    except Exception:
        pass

    logger.warning("Negotiator class not found; startup will continue without one")
    return None



# ========================================================================
#                       STARTUP FUNCTION (FIXED)
# ========================================================================
def perform_kernel_startup(app, cfg: Optional[dict] = None):
    cfg = cfg or {}

    # --------------------------------------------------------------
    # 1. EVENT BUS
    # --------------------------------------------------------------
    event_bus = EventBus()
    app.register("event_bus", event_bus)

    # --------------------------------------------------------------
    # 2. LLM Provider Router + GroqProvider Wiring
    # --------------------------------------------------------------
    router = ProviderRouter()

    groq_api_url = cfg.get("groq_api_url", "https://api.groq.com")
    groq_api_key = cfg.get("groq_api_key")

    try:
        groq = GroqProvider(
            api_url=groq_api_url,
            api_key=groq_api_key,
            model=cfg.get("groq_model", "groq-alpha"),
        )
        router.register_sync(
            "groq",
            groq,
            roles=["dsl", "reasoning", "embed"],
            overwrite=True,
        )
        logger.info("GroqProvider registered successfully")
    except Exception:
        logger.exception("Failed to initialize GroqProvider")

    app.register("llm_provider_router", router)

    # --------------------------------------------------------------
    # 3. MEMORY (Vector + Episodic + Embedder)
    # --------------------------------------------------------------
    vec_db = cfg.get("memory_db_path", "data/memory.db")
    idx_path = cfg.get("memory_index_path", "data/index.ann")
    vec_dim = cfg.get("memory_vector_dim", 1536)

    vec_store = PersistentAnnoyStore(
        meta_db_path=vec_db,
        index_path=idx_path,
        vector_dim=vec_dim,
    )
    epi_store = EpisodicStore(provenance_path=cfg.get("episodic_path", "data/provenance.jsonl"))

    embedder = Embedder(provider=router)  # uses provider → Groq embeddings

    app.register("vector_store", vec_store)
    app.register("episodic_store", epi_store)
    app.register("embedding_service", embedder)

    # --------------------------------------------------------------
    # 4. RUNTIME MANAGERS
    # --------------------------------------------------------------
    trust_mgr = TrustManager(default_level=cfg.get("default_trust_level", "low"))
    identity_mgr = IdentityManager()
    session_dir = cfg.get("session_autosave_dir", "data/sessions")

    session_mgr = SessionManager(
        default_ttl_seconds=cfg.get("session_ttl", 3600),
        autosave_context_dir=session_dir,
    )
    session_mgr.register_trust_manager(trust_mgr)
    session_mgr.register_identity_manager(identity_mgr)

    app.register("trust_manager", trust_mgr)
    app.register("identity_manager", identity_mgr)
    app.register("session_manager", session_mgr)

    # --------------------------------------------------------------
    # 5. SANDBOX + DOCKER
    # --------------------------------------------------------------
    local_adapter = LocalExecutionAdapter(
        work_dir=cfg.get("sandbox_work_dir", "/tmp/titan_sandbox")
    )
    docker_adapter = DockerAdapter(
        image=cfg.get("docker_image", "python:3.11-slim"),
        work_dir=cfg.get("docker_work_dir", "/work"),
        timeout=cfg.get("docker_timeout", 60),
    )

    sandbox = SandboxRunner(
        adapter=local_adapter,
        work_dir=cfg.get("sandbox_work_dir", "/tmp/titan_sandbox"),
        default_timeout=cfg.get("sandbox_timeout", 30),
        policy_engine=_policy_engine,
    )

    app.register("sandbox", sandbox)
    app.register("docker_adapter", docker_adapter)
    app.register("sandbox_cleanup", cleanup_orphaned_containers)

    # --------------------------------------------------------------
    # 6. HOSTBRIDGE
    # --------------------------------------------------------------
    hb = HostBridgeService(
        manifests_dir=cfg.get(
            "hostbridge_manifests_dir",
            "titan/augmentation/hostbridge/manifests",
        ),
        policy_engine=_policy_engine,
    )
    app.register("hostbridge", hb)

    # --------------------------------------------------------------
    # 7. CAPABILITY REGISTRY
    # --------------------------------------------------------------
    caps = CapabilityRegistry()

    caps.register("sandbox", sandbox, metadata={"description": "local sandbox runner"})
    caps.register("docker", docker_adapter, metadata={"description": "docker adapter"})
    caps.register("hostbridge", hb, metadata={"description": "host bridge"})

    app.register("cap_registry", caps)

    # --------------------------------------------------------------
    # 8. PLUGINS (Filesystem + HTTP)
    # --------------------------------------------------------------
    fs = FilesystemPlugin(sandbox_dir=cfg.get("plugin_filesystem_dir", "/tmp/titan_workspace"))
    http = HTTPPlugin(default_timeout=10)

    register_plugin("filesystem", fs)
    register_plugin("http", http)

    app.register("plugin_filesystem", fs)
    app.register("plugin_http", http)

    # --------------------------------------------------------------
    # 9. PARSER (Heuristic + LLM DSL Generator)
    # --------------------------------------------------------------
    dsl_gen = LLMDslGenerator(
        llm_provider=router,
        cap_registry=caps,
        vector_store=vec_store,
        embedder=embedder,
        default_model_role="dsl",
    )

    heuristic = HeuristicParser()
    parser_adapter = ParserAdapter(
        heuristic_parser=heuristic,
        llm_dsl_generator=dsl_gen,
    )

    app.register("parser_adapter", parser_adapter)

    # --------------------------------------------------------------
    # 10. NEGOTIATOR
    # --------------------------------------------------------------
    NegotiatorClass = _import_negotiator()
    negotiator = None

    if NegotiatorClass:
        try:
            negotiator = NegotiatorClass(
                hostbridge=hb,
                sandbox=sandbox,
                policy_engine=_policy_engine,
            )
        except Exception:
            logger.exception("Failed to instantiate Negotiator")
            negotiator = None

    app.register("negotiator", negotiator)

    # --------------------------------------------------------------
    # 11. WORKER POOL (Async-First, Parallel Capable)
    # --------------------------------------------------------------
    worker_pool = WorkerPool(
        max_workers=cfg.get("worker_pool_max_workers", 16),
        thread_workers=cfg.get("worker_thread_workers", 8),
    )
    app.register("worker_pool", worker_pool)

    # --------------------------------------------------------------
    # 12. ORCHESTRATOR (Async Execution Engine)
    # --------------------------------------------------------------
    orch = Orchestrator(
        worker_pool=worker_pool,
        event_emitter=event_bus.publish,
        policy_engine=_policy_engine,
    )

    app.register("orchestrator", orch)

    logger.info("[Kernel] Startup wiring completed (async-first, provider-aware).")

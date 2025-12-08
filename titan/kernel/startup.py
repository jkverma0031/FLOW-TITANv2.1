# titan/kernel/startup.py
from __future__ import annotations
import logging
from typing import Optional

# ==========================================================
# Sandbox & HostBridge
# ==========================================================
from titan.augmentation.sandbox.sandbox_runner import SandboxRunner
from titan.augmentation.sandbox.docker_adapter import DockerAdapter
from titan.augmentation.sandbox.execution_adapter import LocalExecutionAdapter
from titan.augmentation.sandbox.cleanup import cleanup_orphaned_containers

from titan.augmentation.hostbridge.hostbridge_service import HostBridgeService
from titan.augmentation.safety import is_command_safe

# ==========================================================
# Memory subsystem
# ==========================================================
from titan.memory.persistent_annoy_store import PersistentAnnoyStore
from titan.memory.episodic_store import EpisodicStore
from titan.memory.embeddings import Embedder

# ==========================================================
# Runtime managers
# ==========================================================
from titan.runtime.session_manager import SessionManager
from titan.runtime.context_store import ContextStore
from titan.runtime.trust_manager import TrustManager
from titan.runtime.identity import IdentityManager

# ==========================================================
# Executor
# ==========================================================
from titan.executor.orchestrator import Orchestrator
from titan.executor.worker_pool import WorkerPool

# ==========================================================
# Kernel Core
# ==========================================================
from titan.kernel.event_bus import EventBus
from titan.kernel.capability_registry import CapabilityRegistry
from titan.kernel.app_context import _SENTINEL

# ==========================================================
# Parser Subsystem
# ==========================================================
from titan.parser.adapter import ParserAdapter
from titan.parser.heuristic_parser import HeuristicParser
from titan.parser.llm_dsl_generator import LLMDslGenerator

# ==========================================================
# Plugins
# ==========================================================
from titan.runtime.plugins.registry import register_plugin
from titan.runtime.plugins.filesystem import FilesystemPlugin
from titan.runtime.plugins.http import HTTPPlugin

# NEW Plugins
from titan.runtime.plugins.desktop_plugin import DesktopPlugin
from titan.runtime.plugins.browser_plugin import BrowserPlugin

# ==========================================================
# LLM Provider System
# ==========================================================
from titan.models.provider import ProviderRouter
from titan.models.groq_provider import GroqProvider

# ==========================================================
# Policy Engine (optional)
# ==========================================================
try:
    from titan.policy.engine import PolicyEngine
    _policy_engine = PolicyEngine()
except Exception:
    _policy_engine = None

logger = logging.getLogger(__name__)


# ----------------------------------------------------------
# Negotiator safe import helper
# ----------------------------------------------------------
def _import_negotiator():
    try:
        from titan.augmentation.negotiator import Negotiator
        return Negotiator
    except Exception:
        pass

    try:
        import titan.augmentation.negotiator as _mod
        for name in ("Negotiator", "NegotiatorService", "NegotiatorEngine"):
            if hasattr(_mod, name):
                return getattr(_mod, name)
    except Exception:
        pass

    logger.warning("Negotiator not found; continuing without negotiator.")
    return None


# ==========================================================
#                  STARTUP FUNCTION
# ==========================================================
def perform_kernel_startup(app, cfg: Optional[dict] = None):
    cfg = cfg or {}

    # ------------------------------------------------------
    # 1. EVENT BUS
    # ------------------------------------------------------
    event_bus = EventBus()
    app.register("event_bus", event_bus)

    # ------------------------------------------------------
    # 2. LLM Provider Router (Groq integrated)
    # ------------------------------------------------------
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

    # ------------------------------------------------------
    # 3. MEMORY SYSTEM
    # ------------------------------------------------------
    vec_store = PersistentAnnoyStore(
        meta_db_path=cfg.get("memory_db_path", "data/memory.db"),
        index_path=cfg.get("memory_index_path", "data/index.ann"),
        vector_dim=cfg.get("memory_vector_dim", 1536),
    )

    epi_store = EpisodicStore(
        provenance_path=cfg.get("episodic_path", "data/provenance.jsonl")
    )

    embedder = Embedder(provider=router)

    app.register("vector_store", vec_store)
    app.register("episodic_store", epi_store)
    app.register("embedding_service", embedder)

    # ------------------------------------------------------
    # 4. RUNTIME MANAGERS
    # ------------------------------------------------------
    trust_mgr = TrustManager(default_level=cfg.get("default_trust_level", "low"))
    identity_mgr = IdentityManager()
    session_mgr = SessionManager(
        default_ttl_seconds=cfg.get("session_ttl", 3600),
        autosave_context_dir=cfg.get("session_autosave_dir", "data/sessions"),
    )

    session_mgr.register_trust_manager(trust_mgr)
    session_mgr.register_identity_manager(identity_mgr)

    app.register("trust_manager", trust_mgr)
    app.register("identity_manager", identity_mgr)
    app.register("session_manager", session_mgr)

    # ------------------------------------------------------
    # 5. SANDBOX & DOCKER
    # ------------------------------------------------------
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

    # ------------------------------------------------------
    # 6. HOSTBRIDGE
    # ------------------------------------------------------
    hb = HostBridgeService(
        manifests_dir=cfg.get(
            "hostbridge_manifests_dir",
            "titan/augmentation/hostbridge/manifests",
        ),
        policy_engine=_policy_engine,
    )

    app.register("hostbridge", hb)

    # ------------------------------------------------------
    # 7. CAPABILITY REGISTRY
    # ------------------------------------------------------
    caps = CapabilityRegistry()

    caps.register("sandbox", sandbox)
    caps.register("docker", docker_adapter)
    caps.register("hostbridge", hb)

    app.register("cap_registry", caps)

    # ------------------------------------------------------
    # 8. PLUGINS — Filesystem / HTTP / Desktop / Browser
    # ------------------------------------------------------
    fs = FilesystemPlugin(
        sandbox_dir=cfg.get("plugin_filesystem_dir", "/tmp/titan_fs")
    )
    http = HTTPPlugin(default_timeout=10)

    desktop = DesktopPlugin(
        sandbox_dir=cfg.get("plugin_desktop_sandbox", "/tmp/titan_desktop")
    )
    browser = BrowserPlugin(
        headless=cfg.get("browser_headless", True),
        default_storage_dir=cfg.get("browser_storage_dir", ".titan_browser_profiles")
    )

    register_plugin("filesystem", fs)
    register_plugin("http", http)
    register_plugin("desktop", desktop)
    register_plugin("browser", browser)

    app.register("plugin_filesystem", fs)
    app.register("plugin_http", http)
    app.register("plugin_desktop", desktop)
    app.register("plugin_browser", browser)

    # ------------------------------------------------------
    # 9. PARSER (Heuristic + DSL)
    # ------------------------------------------------------
    dsl_gen = LLMDslGenerator(
        llm_provider=router,
        cap_registry=caps,
        vector_store=vec_store,
        embedder=embedder,
        default_model_role="dsl",
    )

    parser_adapter = ParserAdapter(
        heuristic_parser=HeuristicParser(),
        llm_dsl_generator=dsl_gen,
    )

    app.register("parser_adapter", parser_adapter)

    # ------------------------------------------------------
    # 10. NEGOTIATOR
    # ------------------------------------------------------
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
            logger.exception("Failed to initialize Negotiator")
            negotiator = None

    app.register("negotiator", negotiator)

    # ------------------------------------------------------
    # 11. WORKER POOL
    # ------------------------------------------------------
    worker_pool = WorkerPool(
        max_workers=cfg.get("worker_pool_max_workers", 16),
        thread_workers=cfg.get("worker_thread_workers", 8),
    )

    app.register("worker_pool", worker_pool)

    # ------------------------------------------------------
    # 12. ORCHESTRATOR
    # ------------------------------------------------------
    orch = Orchestrator(
        worker_pool=worker_pool,
        event_emitter=event_bus.publish,
        policy_engine=_policy_engine,
    )

    app.register("orchestrator", orch)

    logger.info("[Kernel] Startup wiring completed (async-first, plugin-rich, provider-aware).")

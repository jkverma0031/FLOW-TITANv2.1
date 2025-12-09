# titan/kernel/startup.py
from __future__ import annotations
import logging
import os
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# --- Sandbox & HostBridge ---
from titan.augmentation.sandbox.sandbox_runner import SandboxRunner
from titan.augmentation.sandbox.docker_adapter import DockerAdapter
from titan.augmentation.sandbox.execution_adapter import LocalExecutionAdapter
from titan.augmentation.sandbox.cleanup import cleanup_orphaned_containers

from titan.augmentation.hostbridge.hostbridge_service import HostBridgeService
from titan.augmentation.safety import is_command_safe

# --- Memory subsystem ---
from titan.memory.persistent_annoy_store import PersistentAnnoyStore
from titan.memory.episodic_store import EpisodicStore
from titan.memory.embeddings import Embedder

# --- Runtime managers ---
from titan.runtime.session_manager import SessionManager
from titan.runtime.context_store import ContextStore
from titan.runtime.trust_manager import TrustManager
from titan.runtime.identity import IdentityManager

# --- Executor ---
from titan.executor.orchestrator import Orchestrator
from titan.executor.worker_pool import WorkerPool

# --- Kernel Core ---
from titan.kernel.event_bus import EventBus
from titan.kernel.capability_registry import CapabilityRegistry
from titan.kernel.app_context import _SENTINEL

# --- Parser Subsystem ---
from titan.parser.adapter import ParserAdapter
from titan.parser.heuristic_parser import HeuristicParser
from titan.parser.llm_dsl_generator import LLMDslGenerator

# --- Plugins ---
from titan.runtime.plugins.registry import register_plugin
from titan.runtime.plugins.filesystem import FilesystemPlugin
from titan.runtime.plugins.http import HTTPPlugin
from titan.runtime.plugins.desktop_plugin import DesktopPlugin
from titan.runtime.plugins.browser_plugin import BrowserPlugin

# --- LLM Provider System ---
from titan.models.provider import ProviderRouter
from titan.models.groq_provider import GroqProvider

# --- Policy Engine (optional) ---
try:
    from titan.policy.engine import PolicyEngine
    _policy_engine = PolicyEngine()
except Exception:
    _policy_engine = None


def perform_kernel_startup(app: Any, cfg: Optional[Dict[str, Any]] = None) -> None:
    """
    Robust kernel startup wiring.

    - `app` is expected to implement a simple dict-like registry API:
        app.register(key, value) or app[key] = value
      and to hold state used by other modules.
    - cfg is optional startup configuration.

    This function is defensive: each subsystem initialization is isolated
    with its own try/except, logged, and the app is populated with default
    fallback placeholders where appropriate.
    """
    cfg = cfg or {}

    # provide convenience app.register if missing
    if not hasattr(app, "register"):
        def _reg(key, value):
            try:
                app[key] = value
            except Exception:
                setattr(app, key, value)
        app.register = _reg  # type: ignore

    # set a sensible default_session_id if missing
    default_sid = cfg.get("default_session_id", os.environ.get("TITAN_DEFAULT_SESSION", "default"))
    app.register("default_session_id", default_sid)

    # 1) EventBus
    try:
        event_bus = EventBus(max_workers=cfg.get("eventbus_workers", 8))
        app.register("event_bus", event_bus)
    except Exception:
        logger.exception("Failed to create EventBus")
        app.register("event_bus", None)

    # 2) LLM Provider Router (Groq integrated)
    try:
        router = ProviderRouter()
        groq_api_url = cfg.get("groq_api_url", "https://api.groq.com")
        groq_api_key = cfg.get("groq_api_key")
        groq = GroqProvider(api_url=groq_api_url, api_key=groq_api_key, model=cfg.get("groq_model", "groq-alpha"))
        router.register_sync("groq", groq, roles=["dsl", "reasoning", "embed"], overwrite=True)
        app.register("llm_provider_router", router)
        logger.info("LLM Provider Router initialized (groq registered)")
    except Exception:
        logger.exception("Failed to initialize LLM Provider Router")
        app.register("llm_provider_router", ProviderRouter())

    # 3) Memory system
    try:
        vec_store = PersistentAnnoyStore(
            meta_db_path=cfg.get("memory_db_path", "data/memory.db"),
            index_path=cfg.get("memory_index_path", "data/index.ann"),
            vector_dim=cfg.get("memory_vector_dim", 1536),
        )
        app.register("vector_store", vec_store)
    except Exception:
        logger.exception("PersistentAnnoyStore init failed")
        app.register("vector_store", None)

    try:
        epi_store = EpisodicStore(provenance_path=cfg.get("episodic_path", "data/provenance.jsonl"))
        app.register("episodic_store", epi_store)
    except Exception:
        logger.exception("EpisodicStore init failed")
        app.register("episodic_store", None)

    try:
        embedder = Embedder(provider=app.get("llm_provider_router"))
        app.register("embedding_service", embedder)
    except Exception:
        logger.exception("Embedder init failed")
        app.register("embedding_service", None)

    # 4) Runtime managers
    try:
        trust_mgr = TrustManager(default_level=cfg.get("default_trust_level", "low"))
        identity_mgr = IdentityManager()
        session_mgr = SessionManager(default_ttl_seconds=cfg.get("session_ttl", 3600),
                                     autosave_context_dir=cfg.get("session_autosave_dir", "data/sessions"))
        session_mgr.register_trust_manager(trust_mgr)
        session_mgr.register_identity_manager(identity_mgr)
        app.register("trust_manager", trust_mgr)
        app.register("identity_manager", identity_mgr)
        app.register("session_manager", session_mgr)
    except Exception:
        logger.exception("Runtime managers init failed")
        # register placeholders
        app.register("trust_manager", None)
        app.register("identity_manager", None)
        app.register("session_manager", None)

    # 5) Sandbox & docker
    try:
        local_adapter = LocalExecutionAdapter(work_dir=cfg.get("sandbox_work_dir", "/tmp/titan_sandbox"))
        docker_adapter = DockerAdapter(image=cfg.get("docker_image", "python:3.11-slim"),
                                       work_dir=cfg.get("docker_work_dir", "/work"),
                                       timeout=cfg.get("docker_timeout", 60))
        sandbox = SandboxRunner(adapter=local_adapter,
                                work_dir=cfg.get("sandbox_work_dir", "/tmp/titan_sandbox"),
                                default_timeout=cfg.get("sandbox_timeout", 30),
                                policy_engine=_policy_engine)
        app.register("sandbox", sandbox)
        app.register("docker_adapter", docker_adapter)
        app.register("sandbox_cleanup", cleanup_orphaned_containers)
    except Exception:
        logger.exception("Sandbox/Docker init failed")
        app.register("sandbox", None)
        app.register("docker_adapter", None)

    # 6) HostBridge
    try:
        hb = HostBridgeService(manifests_dir=cfg.get("hostbridge_manifests_dir", "titan/augmentation/hostbridge/manifests"),
                               policy_engine=_policy_engine)
        app.register("hostbridge", hb)
    except Exception:
        logger.exception("HostBridge init failed")
        app.register("hostbridge", None)

    # 7) Capability registry
    try:
        caps = CapabilityRegistry()
        if app.get("sandbox"):
            caps.register("sandbox", app["sandbox"])
        if app.get("docker_adapter"):
            caps.register("docker", app["docker_adapter"])
        if app.get("hostbridge"):
            caps.register("hostbridge", app["hostbridge"])
        app.register("cap_registry", caps)
    except Exception:
        logger.exception("CapabilityRegistry init failed")
        app.register("cap_registry", CapabilityRegistry())

    # 8) Plugins
    try:
        fs = FilesystemPlugin(sandbox_dir=cfg.get("plugin_filesystem_dir", "/tmp/titan_fs"))
        http = HTTPPlugin(default_timeout=cfg.get("plugin_http_timeout", 10))
        desktop = DesktopPlugin(sandbox_dir=cfg.get("plugin_desktop_sandbox", "/tmp/titan_desktop"))
        browser = BrowserPlugin(headless=cfg.get("browser_headless", True),
                                default_storage_dir=cfg.get("browser_storage_dir", ".titan_browser_profiles"))
        register_plugin("filesystem", fs)
        register_plugin("http", http)
        register_plugin("desktop", desktop)
        register_plugin("browser", browser)
        app.register("plugin_filesystem", fs)
        app.register("plugin_http", http)
        app.register("plugin_desktop", desktop)
        app.register("plugin_browser", browser)
    except Exception:
        logger.exception("Plugin init failed")

    # 9) Parser subsystem
    try:
        dsl_gen = LLMDslGenerator(llm_provider=app.get("llm_provider_router"),
                                  cap_registry=app.get("cap_registry"),
                                  vector_store=app.get("vector_store"),
                                  embedder=app.get("embedding_service"),
                                  default_model_role="dsl")
        parser_adapter = ParserAdapter(heuristic_parser=HeuristicParser(), llm_dsl_generator=dsl_gen)
        app.register("parser_adapter", parser_adapter)
    except Exception:
        logger.exception("Parser subsystem init failed")
        app.register("parser_adapter", None)

    # 10) Negotiator (optional)
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

    NegotiatorClass = _import_negotiator()
    if NegotiatorClass:
        try:
            negotiator = NegotiatorClass(hostbridge=app.get("hostbridge"), sandbox=app.get("sandbox"), policy_engine=_policy_engine)
            app.register("negotiator", negotiator)
        except Exception:
            logger.exception("Failed to initialize Negotiator")
            app.register("negotiator", None)
    else:
        app.register("negotiator", None)

    # 11) Worker pool
    try:
        worker_pool = WorkerPool(max_workers=cfg.get("worker_pool_max_workers", 16),
                                 thread_workers=cfg.get("worker_thread_workers", 8))
        app.register("worker_pool", worker_pool)
    except Exception:
        logger.exception("WorkerPool init failed")
        app.register("worker_pool", None)

    # 12) Orchestrator
    try:
        orch = Orchestrator(worker_pool=app.get("worker_pool"), event_emitter=app.get("event_bus").publish if app.get("event_bus") else None, policy_engine=_policy_engine)
        app.register("orchestrator", orch)
    except Exception:
        logger.exception("Orchestrator init failed")
        app.register("orchestrator", None)

    logger.info("[Kernel] Startup wiring completed (defensive mode).")

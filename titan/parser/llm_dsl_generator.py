# titan/parser/llm_dsl_generator.py
from __future__ import annotations
import asyncio
import json
import logging
from typing import Optional, Dict, Any, List

from titan.kernel.capability_registry import CapabilityRegistry
from titan.memory.embeddings import Embedder

logger = logging.getLogger(__name__)

class LLMDslGenerator:
    """
    DSL generator that:
      - Accepts a ProviderRouter or provider instance as llm_provider
      - Injects capability manifests (tools) into the prompt
      - Retrieves relevant memories from vector store and includes them
      - Requests JSON-formatted DSL output for deterministic parsing
    """

    def __init__(self, llm_provider: Optional[Any] = None, cap_registry: Optional[CapabilityRegistry] = None, vector_store: Optional[Any] = None, embedder: Optional[Embedder] = None, default_model_role: str = "dsl"):
        self.llm_provider = llm_provider  # ProviderRouter or provider
        self.cap_registry = cap_registry
        self.vector_store = vector_store
        # If embedder not provided but provider is ProviderRouter, create Embedder delegating to provider
        if embedder is None and llm_provider is not None:
            self.embedder = Embedder(llm_provider)
        else:
            self.embedder = embedder or Embedder(None)
        self.default_model_role = default_model_role

    # ---------------------------
    # Public API
    # ---------------------------
    async def generate_dsl_async(self, user_prompt: str, *, max_tokens: int = 512, temperature: float = 0.0, top_k_mem: int = 5) -> Dict[str, Any]:
        """
        Generate DSL JSON from a natural language prompt.
        Returns dict with keys: { "dsl": <dict-or-str>, "raw": <model-output> }
        """
        # 1) gather tool manifests
        tools = {}
        try:
            if self.cap_registry:
                tools = self.cap_registry.export_manifests()
        except Exception:
            logger.exception("LLMDslGenerator: failed to export manifests")

        # 2) fetch memory context (if vector_store + embedder available)
        memory_snippets: List[str] = []
        try:
            if self.vector_store:
                emb = await self.embedder.embed_async(user_prompt)
                # assume vector_store has a method query(embedding, top_k) returning list of records with 'text' or 'payload'
                try:
                    hits = await self._vector_query_async(emb, top_k=top_k_mem)
                except Exception:
                    # fallback to sync call in threadpool
                    loop = asyncio.get_event_loop()
                    hits = await loop.run_in_executor(None, lambda: self.vector_store.query(emb, top_k=top_k_mem))
                if hits:
                    for h in hits:
                        if isinstance(h, dict):
                            memory_snippets.append(h.get("text") or h.get("payload") or str(h))
                        else:
                            # if result is a simple string or object with .text
                            memory_snippets.append(str(h))
        except Exception:
            logger.exception("LLMDslGenerator: memory retrieval failed; continuing without memory")

        # 3) build prompt with explicit JSON output requirement
        prompt_parts = []
        prompt_parts.append("You are TITAN's DSL generator. Produce a JSON object that represents a plan in the project's DSL.")
        if tools:
            prompt_parts.append("Available tools (manifests):")
            # keep the tool manifest compact
            try:
                shown = {k: tools[k].get("manifest", tools[k]) for k in tools}
                prompt_parts.append(json.dumps(shown, indent=2)[:8000])
            except Exception:
                prompt_parts.append(str(tools))
        if memory_snippets:
            prompt_parts.append("Relevant past memories and results (to help planning):")
            for s in memory_snippets:
                prompt_parts.append(str(s)[:2000])

        prompt_parts.append("User request:")
        prompt_parts.append(user_prompt)
        prompt_parts.append("")
        prompt_parts.append("Produce only a single JSON object (no code fences) with keys: nodes (list), metadata (optional).")
        prompt_parts.append("Each node should be a dict with: id, type (TASK/START/END/PARALLEL), metadata (module/plugin, action_type, command or task_args).")
        prompt_parts.append("If a node should run in parallel with others, use metadata.parallel_group with a shared string.")
        prompt_parts.append("Make the output valid JSON. Use a top-level key 'plan' optionally, but primarily return {'nodes': [...], 'metadata': {...}}.")
        prompt_parts.append("If you cannot create a multi-step plan, return an empty plan: {\"nodes\": []}")

        prompt = "\n\n".join(prompt_parts)

        logger.debug("LLMDslGenerator.generate_dsl_async: sending prompt (len=%d)", len(prompt))

        # 4) call LLM provider (prefer role-based provider)
        if self.llm_provider is None:
            raise RuntimeError("No llm_provider registered for LLMDslGenerator")

        try:
            # provider may be ProviderRouter or provider instance
            if hasattr(self.llm_provider, "complete_async"):
                model_call = getattr(self.llm_provider, "complete_async")
                if asyncio.iscoroutinefunction(model_call):
                    resp = await self.llm_provider.complete_async(prompt, provider_name=None, role=self.default_model_role, max_tokens=max_tokens, temperature=temperature, tools=[{"name":k,"schema":tools[k].get("manifest") if isinstance(tools[k], dict) else tools[k]} for k in tools])
                else:
                    # sync provider: run in threadpool
                    loop = asyncio.get_event_loop()
                    resp = await loop.run_in_executor(None, lambda: self.llm_provider.complete(prompt, tools=[{"name":k,"schema":tools[k].get("manifest") if isinstance(tools[k], dict) else tools[k]} for k in tools], max_tokens=max_tokens, temperature=temperature))
            else:
                raise RuntimeError("llm_provider lacks complete_async/complete method")
        except Exception as e:
            logger.exception("LLMDslGenerator: llm_provider.complete_async failed")
            raise

        model_text = ""
        if isinstance(resp, dict):
            model_text = resp.get("text") or resp.get("raw", "")
        else:
            model_text = str(resp)

        # 5) parse model_text as JSON
        try:
            # try to extract first JSON object from model_text
            obj = json.loads(model_text)
            # guard: if obj has top-level "plan", prefer it
            if isinstance(obj, dict) and "plan" in obj and isinstance(obj["plan"], dict):
                dsl = obj["plan"]
            else:
                dsl = obj
            return {"dsl": dsl, "raw": model_text}
        except Exception:
            # attempt to find a JSON substring
            try:
                start = model_text.find("{")
                end = model_text.rfind("}")
                if start != -1 and end != -1 and end > start:
                    sub = model_text[start:end+1]
                    obj = json.loads(sub)
                    return {"dsl": obj, "raw": model_text}
            except Exception:
                pass
        # parsing failed: return raw and an empty plan
        logger.warning("LLMDslGenerator: failed to parse JSON from model output; returning empty plan")
        return {"dsl": {"nodes": []}, "raw": model_text}

    def generate_dsl(self, user_prompt: str, **kwargs) -> Dict[str, Any]:
        """
        Sync wrapper for convenience.
        """
        coro = self.generate_dsl_async(user_prompt, **kwargs)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return asyncio.run_coroutine_threadsafe(coro, loop).result()
        except RuntimeError:
            pass
        return asyncio.run(coro)

    # ---------------------------
    # Helper: vector query async if supported
    # ---------------------------
    async def _vector_query_async(self, embedding, top_k: int = 5):
        """
        Wrapper to query vector stores that may or may not have async API.
        We attempt embedder-aware call patterns and fallback to sync via threadpool.
        Expected return: list of hits, where hit may be dict with 'text' or 'payload'.
        """
        if self.vector_store is None:
            return []

        # Common interface: vector_store.query(embedding, top_k)
        # If vector_store has async method 'query_async', use it
        if hasattr(self.vector_store, "query_async") and asyncio.iscoroutinefunction(self.vector_store.query_async):
            return await self.vector_store.query_async(embedding, top_k=top_k)
        # fallback to sync, run in threadpool
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: getattr(self.vector_store, "query")(embedding, top_k))

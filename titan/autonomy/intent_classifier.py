# titan/autonomy/intent_classifier.py
from __future__ import annotations
import asyncio
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class IntentClassifierError(Exception):
    pass

class IntentClassifier:
    """
    Classifies natural language / event payloads into structured intents using the
    registered ProviderRouter or any provider available in the kernel.
    """

    def __init__(self, provider_router: Optional[Any] = None, config: Optional[Any] = None):
        self.provider_router = provider_router
        self.config = config or {}
        # prompt template: returns JSON {"intent":"...", "confidence":0.0, "params": {...}}
        self._prompt_template = (
            "You are an intent classifier for an autonomous agent. "
            "Given the following event and context, return a compact JSON object with keys: "
            '"intent" (string), "confidence" (0.0-1.0), "params" (object). '
            "Event: {event}\nContext: {context}\nReturn only JSON."
        )

    async def classify_async(self, event: Dict[str, Any], context: Optional[Dict[str, Any]] = None, *, max_tokens: Optional[int] = None, temperature: Optional[float] = None) -> Dict[str, Any]:
        """
        Classify the event into an intent.
        Returns dict { "intent": str, "confidence": float, "params": dict, "raw": <model output> }
        """
        ctx = context or {}
        prompt = self._prompt_template.format(event=event, context=ctx)
        max_tokens = max_tokens or getattr(self.config, "intent_max_tokens", 256)
        temperature = temperature if temperature is not None else getattr(self.config, "intent_temp", 0.0)

        # choose provider: prefer provider_router with role "reasoning" or provided fallback
        provider = self.provider_router
        try:
            if provider is None:
                raise IntentClassifierError("No LLM provider/router available for intent classification")

            # Many ProviderRouters expose complete_async(role=...), allow both router and provider objects
            if hasattr(provider, "complete_async"):
                # preferred route (router)
                resp = await provider.complete_async(prompt, provider_name=None, role=getattr(self.config, "intent_role", "reasoning"), max_tokens=max_tokens, temperature=temperature)
            elif hasattr(provider, "complete"):
                loop = asyncio.get_event_loop()
                resp = await loop.run_in_executor(None, lambda: provider.complete(prompt, max_tokens=max_tokens, temperature=temperature))
            else:
                raise IntentClassifierError("Provider does not support completion")

            raw_text = ""
            if isinstance(resp, dict):
                raw_text = resp.get("text") or str(resp.get("raw") or resp)
            else:
                raw_text = str(resp)
        except Exception as e:
            logger.exception("IntentClassifier: provider call failed")
            raise IntentClassifierError(str(e))

        # parse JSON out of raw_text
        try:
            import json, re
            # attempt direct parse
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict) and "intent" in parsed:
                parsed.setdefault("confidence", float(parsed.get("confidence", 0.0)))
                parsed.setdefault("params", parsed.get("params", {}))
                parsed["raw"] = raw_text
                return parsed
            # fallback: extract JSON substring
            start = raw_text.find("{")
            end = raw_text.rfind("}")
            if start != -1 and end != -1 and end > start:
                sub = raw_text[start:end+1]
                parsed = json.loads(sub)
                parsed.setdefault("confidence", float(parsed.get("confidence", 0.0)))
                parsed.setdefault("params", parsed.get("params", {}))
                parsed["raw"] = raw_text
                return parsed
        except Exception:
            logger.debug("IntentClassifier: JSON parse failed, attempting heuristic extraction")

        # heuristic fallback: simple mapping
        text = raw_text.strip().lower()
        intent = "unknown"
        confidence = 0.0
        params = {}
        if "open" in text and ("file" in text or "document" in text):
            intent = "open_file"; confidence = 0.4
        if "visit" in text or "browse" in text or "open website" in text:
            intent = "open_url"; confidence = 0.5
        if "call" in text or "reply" in text:
            intent = "reply_or_call"; confidence = 0.45
        if "summarize" in text or "summarise" in text:
            intent = "summarize"; confidence = 0.7

        return {"intent": intent, "confidence": confidence, "params": params, "raw": raw_text}

    def classify(self, *args, **kwargs) -> Dict[str, Any]:
        coro = self.classify_async(*args, **kwargs)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return asyncio.run_coroutine_threadsafe(coro, loop).result()
        except RuntimeError:
            pass
        return asyncio.run(coro)

# titan/planner/planner.py
from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, List, Any, Dict, Tuple

from titan.planner.intent_modifier import modify_intent
from titan.parser.llm_dsl_generator import LLMDslGenerator
from titan.planner.dsl.ir_dsl import parse_dsl, ASTRoot
from titan.planner.dsl.ir_compiler import Compiler
from titan.models.provider import LLMProvider

# Plan schema / status
try:
    from titan.schemas.plan import Plan, PlanStatus, new_plan_id
except Exception:
    # Defensive fallback if schema import fails during tests
    Plan = None
    PlanStatus = None
    def new_plan_id(prefix: str = "plan") -> str:
        import uuid
        return f"{prefix}_{uuid.uuid4().hex[:8]}"

logger = logging.getLogger(__name__)

@dataclass
class PlannerConfig:
    max_rewrite_attempts: int = 3
    rewrite_backoff_seconds: float = 0.5
    planner_name: str = "titan_planner_v2.1"


class Planner:
    """
    The Planner coordinates:
      - intent modification (deterministic),
      - optional memory retrieval (few-shot),
      - LLM DSL generation,
      - parse/validate + rewrite loop,
      - AST -> CFG compilation,
      - Plan object construction.

    It requires an LLM provider (LLMProvider) and optionally a VectorStore-like object
    providing a `query(embedding, k)` API for few-shot retrieval.
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        *,
        config: Optional[PlannerConfig] = None,
        vector_store: Optional[Any] = None,
        dsl_generator: Optional[LLMDslGenerator] = None,
    ):
        self.llm = llm_provider
        self.config = config or PlannerConfig()
        self.vector_store = vector_store
        # Use provided DSL generator or create a default one
        self.dsl_generator = dsl_generator or LLMDslGenerator(self.llm)

    async def plan(
        self,
        session_id: str,
        user_instruction: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        High-level entrypoint to produce a Plan object.
        Returns the Plan Pydantic model (if available) or a dict with plan information.
        """
        context = context or {}
        start_ts = time.time()
        logger.info("Planner: starting plan generation for session=%s", session_id)

        # 1) Intent modification (deterministic pre-processing)
        try:
            modified = modify_intent(user_instruction, context)
            logger.debug("Intent modified: %s -> %s", user_instruction, modified)
        except Exception as e:
            logger.exception("Intent modifier failed; falling back to original instruction.")
            modified = user_instruction

        # 2) Memory retrieval (optional) - use vector_store to create few-shot examples
        examples: List[str] = []
        try:
            if self.vector_store is not None:
                # Simple approach: embed the user instruction and fetch K examples
                if hasattr(self.llm, "embed"):
                    emb = self.llm.embed(modified)
                    results = self.vector_store.query(emb, k=3)
                    for r in results:
                        # Expect metadata with 'dsl' or 'dsl_text'
                        meta = r.get("metadata", {}) if isinstance(r, dict) else {}
                        ex = meta.get("dsl") or meta.get("dsl_text") or meta.get("text") or None
                        if ex:
                            examples.append(ex)
                else:
                    logger.debug("LLM provider has no embed method; skipping memory retrieval.")
        except Exception as e:
            logger.exception("Memory retrieval failed; continuing without examples.")

        # 3) Ask LLM to generate DSL
        try:
            dsl_text = self.dsl_generator.generate(modified, examples=examples)
            logger.info("LLM produced DSL (len=%d)", len(dsl_text))
        except Exception as e:
            logger.exception("LLM DSL generation failed.")
            raise

        # 4) Validate parse & possibly ask for rewrite
        ast_root, final_dsl = await self._validate_or_rewrite(dsl_text)

        # 5) Compile AST -> CFG (IR compiler)
        try:
            compiler = Compiler()
            cfg_nodes = compiler.compile(ast_root)  # returns list/dict nodes per ir_compiler contract
            logger.info("Compiled CFG with %d nodes", len(cfg_nodes) if cfg_nodes else 0)
        except Exception as e:
            logger.exception("Compilation failed.")
            raise

        # 6) Build Plan Pydantic object if available, else return a dict
        plan_id = new_plan_id("plan")
        created_at = int(time.time())
        try:
            if Plan is not None:
                plan = Plan(
                    id=plan_id,
                    session_id=session_id,
                    dsl_text=final_dsl,
                    parsed_ast=ast_root,  # many Plan schemas will accept an AST or serialized form
                    cfg=cfg_nodes,
                    status=PlanStatus.CREATED if PlanStatus is not None else "created",
                    created_at=created_at,
                    metadata={"planner": self.config.planner_name, "examples_used": len(examples)},
                )
                logger.info("Plan object created id=%s", plan_id)
                return plan
            else:
                # Return a fallback plain dict
                return {
                    "id": plan_id,
                    "session_id": session_id,
                    "dsl_text": final_dsl,
                    "parsed_ast": ast_root,
                    "cfg": cfg_nodes,
                    "status": "created",
                    "created_at": created_at,
                    "metadata": {"planner": self.config.planner_name, "examples_used": len(examples)},
                }
        finally:
            elapsed = time.time() - start_ts
            logger.info("Planner: finished plan generation for session=%s in %.2fs", session_id, elapsed)

    async def _validate_or_rewrite(self, dsl_text: str) -> Tuple[ASTRoot, str]:
        """
        Try parsing DSL -> ASTRoot. If parser raises an exception, call the LLM to rewrite
        the DSL by providing the parser error and requesting a corrected DSL snippet.

        Returns (ast_root, final_dsl_text)
        """
        attempts = 0
        current_text = dsl_text

        while attempts <= self.config.max_rewrite_attempts:
            attempts += 1
            try:
                ast_root = parse_dsl(current_text)
                # parse successful
                return ast_root, current_text
            except Exception as parse_err:
                logger.warning("DSL parse error on attempt %d: %s", attempts, parse_err)
                if attempts > self.config.max_rewrite_attempts:
                    logger.error("Max rewrite attempts reached; raising parse error.")
                    raise

                # Build a focused rewrite prompt
                error_msg = str(parse_err)
                rewrite_prompt = self._build_rewrite_prompt(current_text, error_msg, attempt=attempts)
                logger.debug("Asking LLM to rewrite DSL (attempt %d)", attempts)
                try:
                    rewritten = self.dsl_generator.generate(rewrite_prompt, examples=None)
                    # The generator returns DSL text. Replace current_text with rewritten.
                    # If the model returns extra commentary, the generator should have cleaned it.
                    current_text = rewritten
                except Exception as e:
                    logger.exception("LLM failed to rewrite DSL.")
                    # small backoff then retry loop (subject to max attempts)
                    await asyncio.sleep(self.config.rewrite_backoff_seconds * attempts)
                    continue

                # small backoff before re-parse
                await asyncio.sleep(self.config.rewrite_backoff_seconds * attempts)

        # If somehow the loop ends without a return (shouldn't), raise
        raise RuntimeError("Failed to parse DSL after rewrite attempts.")

    def _build_rewrite_prompt(self, bad_dsl: str, parse_error: str, attempt: int = 1) -> str:
        """
        Constructs a rewrite prompt: explain the parse error and ask for corrected DSL only.
        We provide the failing DSL and the parser traceback/text. The LLM is asked to return
        the corrected DSL ONLY.
        """
        header = (
            "The following TITAN DSL failed to parse. Please RETURN ONLY the corrected TITAN DSL.\n"
            "Do NOT provide explanations or JSON. Fix the exact syntax error(s) and ensure the result\n"
            "complies with the grammar. If you cannot fix, return the smallest corrected DSL snippet."
        )
        prompt = "\n\n".join([
            header,
            "PARSER ERROR:",
            parse_error,
            "FAILING DSL:",
            bad_dsl,
            f"(Attempt {attempt})",
            # provide optional checklist from helper prompts
            getattr(__import__("titan.planner.dsl.llm_helper_prompts", fromlist=["REWRITE_CHECKLIST"]), "REWRITE_CHECKLIST", None) or ""
        ])
        return prompt

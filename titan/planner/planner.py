# Path: FLOW/titan/planner/planner.py
"""
The TITANv2.1 Planner:
- Accepts natural language input + runtime context
- Modifies intent (clarifies pronouns, resolves references)
- Extracts semantic frames
- Queries memory for similar past tasks / DSL patterns
- Builds LLM prompts
- Produces raw DSL
- Parses DSL -> AST
- Validates AST
- Runs DSL rewrite loop if needed
- Compiles AST -> CFG
- Builds Plan() object (without executing anything)
"""

from __future__ import annotations
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass
import logging

from titan.schemas.plan import Plan, PlanStatus
from titan.schemas.task import Task
from titan.schemas.graph import CFG

from titan.memory.vector_store import VectorStore
from titan.runtime.context_store import ContextStore
from titan.runtime.trust_manager import TrustManager

from titan.planner.dsl.ir_dsl import parse_dsl
from titan.planner.dsl.ir_validator import validate_ast
from titan.planner.dsl.ir_compiler import compile_ast_to_cfg
from titan.planner.dsl.llm_helper_prompts import (
    build_generation_prompt,
    build_rewrite_prompt,
)

from titan.planner.intent_modifier import modify_intent
from titan.planner.frame_parser import FrameParser
from titan.planner.task_extractor import extract_task_hints
from titan.planner.router import Router

logger = logging.getLogger(__name__)


@dataclass
class PlannerConfig:
    max_rewrite_attempts: int = 3
    use_memory_examples: bool = True
    memory_retrieval_k: int = 5


class Planner:
    def __init__(
        self,
        llm_client,
        vector_memory: VectorStore,
        router: Router,
        config: PlannerConfig = PlannerConfig(),
    ):
        """
        llm_client: wrapper around OpenAI/Groq/Gemini/Ollama API
        vector_memory: persistent semantic memory
        router: determines which capabilities exist
        """
        self.llm = llm_client
        self.memory = vector_memory
        self.router = router
        self.config = config

    # -------------------------------------------------------------------------
    # MAIN ENTRY
    # -------------------------------------------------------------------------
    async def plan(
        self,
        user_input: str,
        session_id: str,
        context: ContextStore,
        trust: TrustManager,
    ) -> Plan:
        """
        Generate a full Plan object:
        - modifies intent
        - retrieves memory hints
        - requests DSL from LLM
        - validates + rewrites if needed
        - compiles DSL -> CFG
        - extracts tasks mapped from DSL
        - returns Plan object (NOT executed)
        """

        logger.info(f"[Planner] Starting planning for session {session_id}")

        # 1) Modify intent based on context and trust
        resolved_text = modify_intent(user_input, context=context)
        logger.debug(f"[Planner] Intent modified: {resolved_text!r}")

        # 2) Semantic frame extraction
        frames = FrameParser().parse(resolved_text)

        # 3) Task hints (used for DSL prompt enrichment)
        task_hints = extract_task_hints(frames)

        # 4) Retrieve memory examples to help LLM
        memory_examples = []
        if self.config.use_memory_examples:
            memory_examples = await self._retrieve_memory_examples(user_input)

        # 5) Ask LLM for DSL
        prompt = build_generation_prompt(
            user_input=resolved_text,
            context_snippets=task_hints,
            memory_examples=memory_examples,
        )
        raw_dsl = await self.llm.complete(prompt)

        logger.debug(f"[Planner] Initial DSL from LLM:\n{raw_dsl}")

        # 6) Parse + validate DSL (rewrite loop)
        dsl, ast = await self._validate_or_rewrite(raw_dsl)

        # 7) Compile AST -> CFG
        cfg = compile_ast_to_cfg(ast)

        # 8) Extract tasks from CFG's TaskNodes
        tasks = self._build_tasks_from_cfg(cfg)

        # 9) Create Plan
        # --- AFTER DSL, AST, VALIDATION, and CFG ARE READY ---

        from dataclasses import asdict
        from pydantic import ValidationError

        # Convert AST to plain dict (schema expects dict, not ASTRoot object)
        try:
            ast_dict = asdict(ast)
        except Exception:
            # Fallback (should not be needed because AST is dataclasses)
            ast_dict = ast.__dict__

        try:
            plan = Plan(
                dsl_text=dsl,              # FIXED
                parsed_ast=ast_dict,       # FIXED
                cfg=cfg,
                status=PlanStatus.CREATED,
                metadata={
                    "frames": frames,
                    "task_hints": task_hints,
                    "session": session_id,
                },
            )
        except ValidationError as e:
            logger.error(f"[Planner] Plan schema mismatch: {e}")
            raise

        logger.info(f"[Planner] Plan created ID={plan.id}")
        return plan

    # -------------------------------------------------------------------------
    # REWRITE LOOP
    # -------------------------------------------------------------------------
    async def _validate_or_rewrite(self, dsl_text: str) -> Tuple[str, Any]:
        """
        Validate DSL → AST; if errors detected → use rewrite prompt.
        Attempts up to N times defined in PlannerConfig.
        """
        attempts = 0
        current_dsl = dsl_text

        while attempts < self.config.max_rewrite_attempts:
            attempts += 1
            try:
                ast = parse_dsl(current_dsl)
            except Exception as e:
                logger.warning(f"[Planner] DSL parse error: {e}")
                rewrite_prompt = build_rewrite_prompt(
                    original_dsl=current_dsl,
                    errors=[{"message": f"Parse error: {str(e)}", "lineno": None}],
                    warnings=[],
                )
                current_dsl = await self.llm.complete(rewrite_prompt)
                continue

            vr = validate_ast(ast)
            if vr.ok():
                logger.debug("[Planner] DSL validation succeeded.")
                return current_dsl, ast

            # Otherwise, produce rewrite prompt
            logger.warning(f"[Planner] DSL validation failed: {len(vr.errors)} errors")
            err_list = [{"message": e.message, "lineno": e.lineno} for e in vr.errors]
            warn_list = [{"message": w.message, "lineno": w.lineno} for w in vr.warnings]

            rewrite_prompt = build_rewrite_prompt(
                original_dsl=current_dsl,
                errors=err_list,
                warnings=warn_list,
            )
            current_dsl = await self.llm.complete(rewrite_prompt)

        raise ValueError("[Planner] DSL rewrite attempts exceeded limit")

    # -------------------------------------------------------------------------
    # MEMORY RETRIEVAL
    # -------------------------------------------------------------------------
    async def _retrieve_memory_examples(self, query: str) -> List[str]:
        """
        Search the persistent vector memory for relevant DSL/plan examples.
        Returns list of text snippets to feed LLM.
        """
        try:
            hits = await self.memory.query_by_text(
                query=query,
                top_k=self.config.memory_retrieval_k,
            )
            return [h.text for h in hits]
        except Exception as e:
            logger.error(f"[Planner] Memory retrieval error: {e}")
            return []

    # -------------------------------------------------------------------------
    # TASK EXTRACTION FROM CFG
    # -------------------------------------------------------------------------
    def _build_tasks_from_cfg(self, cfg: CFG) -> Dict[str, Task]:
        """
        Translate TaskNodes → concrete titan.schemas.task.Task objects.
        TaskNode.task_ref may be either assignment var or a generated id.
        """
        tasks = {}
        for nid, node in cfg.nodes.items():
            if node.type != node.type.TASK:
                continue

            # name comes as "task:<callname>"
            call_name = node.name.split(":", 1)[-1]

            # DSL args preserved in metadata
            dsl_meta = node.metadata.get("dsl_call", {})
            args = dsl_meta.get("args", {})

            task = Task(
                name=call_name,
                args=args,
                metadata={"from_node_id": nid},
            )
            tasks[node.task_ref] = task

        return tasks

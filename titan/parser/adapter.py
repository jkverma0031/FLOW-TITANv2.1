# Path: titan/parser/adapter.py
"""
FLOW–TITANv2.1 Parser Adapter

This module forms the "bridge" between:
 - Natural language input
 - Optional LLM DSL generation
 - Deterministic Lark parser (parse_dsl) → ASTRoot

IMPORTANT:
 - LLM NEVER returns an AST.
 - LLM ALWAYS returns DSL only.
 - AST is ALWAYS produced by ir_dsl.parse_dsl().
"""

from __future__ import annotations
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class ParserAdapter:
    """
    High-level interface used by Planner.

    Usage:
        ast_root = adapter.parse_user_text("download file and extract zip")
    """

    def __init__(
        self,
        heuristic_parser=None,
        llm_dsl_generator=None,
        prefer_llm: bool = True,
    ):
        self.heuristic = heuristic_parser          # fallback parser → ASTRoot
        self.dsl_gen = llm_dsl_generator           # LLM → DSL text
        self.prefer_llm = prefer_llm

    # --------------------------------------------------------------
    # MAIN ENTRYPOINT
    # --------------------------------------------------------------
    def parse_user_text(self, text: str):
        """
        Natural language → DSL (optional LLM) → ASTRoot (via Lark parser)
        """
        dsl = None

        # --- Step 1: Try LLM DSL generation -----------------------
        if self.prefer_llm and self.dsl_gen:
            try:
                dsl = self.dsl_gen.generate_dsl(text).strip()
                if not dsl:
                    logger.warning("LLM returned empty DSL; falling back.")
                    dsl = None
            except Exception as e:
                logger.warning("LLM DSL generation failed: %s", e)
                dsl = None

        # --- Step 2: If LLM failed → attempt heuristic parser ------
        if dsl is None and self.heuristic:
            try:
                ast = self.heuristic.parse(text)
                return ast
            except Exception as e:
                logger.warning("Heuristic parser failed: %s", e)

        # --- Step 3: If LLM produced DSL → parse with Lark ---------
        if dsl:
            return self.parse_dsl_text(dsl)

        # --- Step 4: FINAL fallback: wrap text into a trivial DSL --
        logger.warning("Falling back to trivial auto-generated task DSL")
        trivial_dsl = f'task "auto_task":\n    run "echo {text.strip()[:50]}"\n'
        return self.parse_dsl_text(trivial_dsl)

    # --------------------------------------------------------------
    # DIRECT DSL → AST
    # --------------------------------------------------------------
    def parse_dsl_text(self, dsl_text: str):
        """Parse DSL → ASTRoot using ir_dsl.parse_dsl()."""
        try:
            from titan.planner.dsl.ir_dsl import parse_dsl
            ast_root = parse_dsl(dsl_text)
            return ast_root
        except Exception as e:
            logger.error("DSL parse error: %s\nDSL was:\n%s", e, dsl_text)
            raise

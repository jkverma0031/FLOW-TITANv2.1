# Path: titan/parser/heuristic_parser.py
"""
Heuristic fallback parser.

This parser directly produces ASTRoot objects WITHOUT using DSL.
It is a safe fallback for when LLM DSL generation fails or DSL grammar fails.

It produces a very simple AST structure using your AST dataclasses:
    ASTRoot → [ASTTaskCall]
"""

from __future__ import annotations
from typing import Dict, List
import logging
import time, uuid
from titan.planner.dsl.ir_dsl import (
    ASTRoot,
    ASTTaskCall,
    ASTValue,
)

logger = logging.getLogger(__name__)


def _new_id(prefix="task"):
    return f"{prefix}_{uuid.uuid4().hex[:6]}_{int(time.time()*1000):x}"


class HeuristicParser:
    """
    Extremely conservative parser:
    - One task per sentence
    - No control-flow constructs
    - Minimal ASTRoot
    """

    def parse(self, text: str):
        sentences = [s.strip() for s in text.split("\n") if s.strip()]
        statements = []

        for s in sentences:
            node = ASTTaskCall(
                name="task",
                args={"name": ASTValue(value=s), "run": ASTValue(value=f"echo '{s}'")},
                lineno=None,
            )
            statements.append(node)

        return ASTRoot(statements=statements, source=text)

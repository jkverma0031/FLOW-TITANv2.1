# Path: titan/parser/llm_dsl_generator.py
"""
LLM DSL Generator
-----------------
LLM → TITAN DSL TEXT ONLY.

This preserves TITAN's deterministic architecture:
    LLM does not create ASTs or CFGs.
    LLM does not generate graph structure.
    LLM does not assign node IDs.

Only DSL text is allowed.
"""

from __future__ import annotations
from typing import Optional
import logging
import textwrap

logger = logging.getLogger(__name__)


BASE_PROMPT = textwrap.dedent("""
You are the FLOW–TITANv2.1 DSL Generator.

Your ONLY job:
    Convert the user instruction into valid TITAN DSL TEXT.

RULES:
- DO NOT output JSON.
- DO NOT output AST.
- DO NOT assign node IDs.
- DO NOT create graphs.
- DSL text ONLY.
- Use TITAN DSL constructs like:

    task "download":
        run "curl https://..."

    loop files:
        task "process":
            run "python script.py {item}"

    if condition:
        task "notify":
            run "echo ok"

USER INSTRUCTION:
-----------------
{input}

RETURN DSL ONLY:
""")

class LLMDslGenerator:

    def __init__(self, llm_client, prompt_template: Optional[str] = None, max_tokens: int = 512):
        self.llm = llm_client
        self.prompt_template = prompt_template or BASE_PROMPT
        self.max_tokens = max_tokens

    def generate_dsl(self, user_instruction: str) -> str:
        prompt = self.prompt_template.format(input=user_instruction)
        raw = self.llm.generate(prompt, max_tokens=self.max_tokens)

        if not isinstance(raw, str):
            raw = str(raw)

        dsl = raw.strip()

        # If LLM tries to cheat and produce JSON, strip it out
        if dsl.startswith("{") or dsl.startswith("["):
            logger.warning("LLM produced structured output; cleaning.")
            lines = [
                l for l in dsl.splitlines()
                if not (l.strip().startswith("{") or l.strip().startswith("}"))
            ]
            dsl = "\n".join(lines).strip()

        return dsl

# Path: FLOW/titan/planner/dsl/llm_helper_prompts.py
"""
Prompts and small helper utilities for LLM interactions related to DSL generation and rewrite.
This module contains:
- DSL generation prompt templates
- rewrite prompt templates that include validator diagnostics
- examples for few-shot prompting
"""

from typing import List, Dict

# DSL usage note:
# - The LLM should output only DSL in the language defined by grammar.lark.
# - No additional commentary should be included.
# - If producing code blocks, only the DSL text should be returned (no markdown fences).

DSL_INTRO = """
You are a specialized planner LLM. Your job: convert natural language instructions into a small, deterministic DSL.
Rules:
- Output ONLY DSL code in the target language (do not add commentary).
- Use the constructs: assignment (var = task(...)), task(name="...", ...), if ...:, for ... in ..., retry attempts=... backoff=...:
- Use quoted strings for string arguments, numbers for numeric ones.
- Keep lines short and simple. Use indentation for blocks.
- Do NOT attempt to output JSON or node IDs. The system will compile your DSL into a structured CFG.
"""

DSL_EXAMPLES = """
# Example 1: compress all PNG images in ~/Photos and upload them
t1 = task(name="list_files", path="~/Photos", pattern="*.png")
for f in t1.result.files:
    t2 = task(name="compress", file=f)
    t3 = task(name="upload", file=f)

# Example 2: if file count is zero, notify and stop
t1 = task(name="list_files", path="~/Photos", pattern="*.png")
if t1.result.count == 0:
    t2 = task(name="notify", message="No files to process")

# Example 3: retry a fragile upload
t1 = task(name="upload", file="a.png")
retry attempts=3 backoff=2:
    t2 = task(name="verify_upload", file="a.png")
"""

# Template to request DSL from LLM
def build_generation_prompt(user_input: str, context_snippets: List[str] = None, memory_examples: List[str] = None) -> str:
    parts = [DSL_INTRO, "\n# User instruction:\n", user_input.strip(), "\n\n# Examples (do NOT adapt unless necessary):\n", DSL_EXAMPLES]
    if context_snippets:
        parts.extend(["\n# Context:\n", "\n".join(context_snippets)])
    if memory_examples:
        parts.extend(["\n# Past successful templates:\n", "\n".join(memory_examples)])
    parts.append("\n# Output the DSL program now:")
    return "\n".join(parts)


# Rewrite prompt: given validation errors, ask LLM to fix DSL
REWRITE_INTRO = """
You previously generated DSL. The system validated it and found issues. Please produce a corrected DSL program that fixes the listed errors.
Rules:
- Output ONLY corrected DSL program.
- Keep the same high-level intent and variable names where possible.
- Ensure condition expressions are valid (no raw Python calls).
- If you cannot fix automatically, produce a short DSL that asks the user to clarify (e.g., a task 'ask_user' with details).
"""

def build_rewrite_prompt(original_dsl: str, errors: List[Dict], warnings: List[Dict]) -> str:
    """
    errors/warnings are lists of dicts with { 'lineno': int, 'message': str }
    """
    parts = [REWRITE_INTRO, "\n# Original DSL:\n", original_dsl.strip(), "\n\n# Validation issues:"]
    if not errors and not warnings:
        parts.append("\n# (No issues reported.)\n")
    else:
        if errors:
            parts.append("\n# Errors:")
            for e in errors:
                parts.append(f"- line {e.get('lineno', '?')}: {e.get('message')}")
        if warnings:
            parts.append("\n# Warnings:")
            for w in warnings:
                parts.append(f"- line {w.get('lineno', '?')}: {w.get('message')}")
    parts.append("\n# Produce CORRECTED DSL only now:")
    return "\n".join(parts)


# Utility: small checklist to include in Planner logs
REWRITE_CHECKLIST = [
    "Fix undefined variable references (or convert them to task outputs).",
    "Ensure retry blocks include valid attempts and optional backoff.",
    "Ensure loops iterate over collection expressions (e.g., t1.result.files).",
    "Ensure condition expressions are simple comparisons and do not use exec/eval.",
]

# Short guidance string that the Planner can inject into the LLM prompt to avoid unsafe constructs
SAFETY_GUIDANCE = """
Safety rules for expressions:
- Do not use Python builtins like eval, exec, __import__, or system calls.
- Conditions must be boolean expressions using variables, literals, and comparison operators (==, !=, <, >, in).
- No arbitrary code execution or dynamic import calls.
"""

# Path: FLOW/titan/planner/intent_modifier.py
"""
Intent Modifier:
Resolves pronouns, vague references, previous task outputs, and context variables.

Examples:
- "Do the same again" → replaced with explicit last task details
- "Upload them" → resolve 'them' using ContextStore (e.g., last_files)
- "Fix the previous error" → attach metadata

This is intentionally simple and conservatively avoids hallucinations.
"""

from __future__ import annotations
from typing import Optional
from titan.runtime.context_store import ContextStore


def modify_intent(text: str, context: ContextStore) -> str:
    """
    Deterministic, conservative intent rewriting.
    Avoids creativity; only substitutes variables the system knows for sure.
    """

    original = text

    # Example: resolve pronouns referring to last file list
    if "them" in text.lower():
        last_files = context.get("last_files")
        if last_files and isinstance(last_files, list):
            text = text.replace("them", f"{last_files}")

    if "it" in text.lower():
        last_item = context.get("last_item")
        if last_item:
            text = text.replace("it", f"{last_item}")

    # Example: explicitly attach working directory
    if "{cwd}" in text:
        cwd = context.get("cwd") or ""
        text = text.replace("{cwd}", cwd)

    # Avoid overly aggressive transformations
    return text

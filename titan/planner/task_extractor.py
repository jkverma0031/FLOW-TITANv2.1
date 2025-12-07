# Path: FLOW/titan/planner/task_extractor.py
"""
Task Extractor:
Converts semantic frames → structured textual hints for the DSL prompt.

Example:
Frames:
  actions = ["compress", "upload"]
  directories = ["~/Photos"]
  file_types = [".png"]
→ Hints:
  - "Likely tasks: compress, upload"
  - "Target directory: ~/Photos"
  - "File types: .png"
"""

from __future__ import annotations
from typing import List, Dict, Any


def extract_task_hints(frames: Dict[str, Any]) -> List[str]:
    hints = []

    if "actions" in frames:
        actions = ", ".join(frames["actions"])
        hints.append(f"Likely tasks: {actions}")

    if "directories" in frames:
        dirs = ", ".join(frames["directories"])
        hints.append(f"Target directories: {dirs}")

    if "file_types" in frames:
        fts = ", ".join(frames["file_types"])
        hints.append(f"File types mentioned: {fts}")

    # Always add the raw text
    hints.append(f"Raw instruction: {frames.get('raw', '')}")

    return hints

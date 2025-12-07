# Path: FLOW/titan/planner/frame_parser.py
"""
FrameParser:
Extracts simple semantic "frames" from natural language.

Frames include:
- action verbs (upload, compress, analyze)
- objects (images, files, directory patterns)
- conditions (if empty, if exists)
- quantities (all, first, last)

These frames help build richer LLM prompts without enforcing a schema.
"""

from __future__ import annotations
from typing import Dict, Any, List
import re


class FrameParser:
    ACTION_RE = re.compile(r"\b(upload|compress|analyze|organize|sort|clean|read|write|list)\b", re.I)
    FILETYPE_RE = re.compile(r"\b(\.png|\.jpg|\.txt|\.json|\.csv)\b", re.I)
    DIRECTORY_RE = re.compile(r"~/[A-Za-z0-9_/]+")

    def parse(self, text: str) -> Dict[str, Any]:
        frames = {}

        # Detect actions
        actions = self.ACTION_RE.findall(text)
        if actions:
            frames["actions"] = list({a.lower() for a in actions})

        # Detect file extensions
        ftypes = self.FILETYPE_RE.findall(text)
        if ftypes:
            frames["file_types"] = list({f.lower() for f in ftypes})

        # Detect directory references
        dirs = self.DIRECTORY_RE.findall(text)
        if dirs:
            frames["directories"] = dirs

        # Add raw text as fallback
        frames["raw"] = text
        return frames

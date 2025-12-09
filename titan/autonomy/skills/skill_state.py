# titan/autonomy/skills/skill_state.py
from __future__ import annotations
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
import time

class SkillState(BaseModel):
    """
    Persistent representation of a skill's durable state.
    Stored in SessionManager under session.context['skills'][<skill_name>]
    """
    enabled: bool = True
    autonomy_mode: Optional[str] = Field(None, description="Per-skill override: 'hybrid'|'ask_first'|'full'")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    last_action_at: float = 0.0
    last_tick_at: float = 0.0

    model_config = {"extra": "forbid"}

    def touch_action(self) -> None:
        self.last_action_at = time.time()

    def touch_tick(self) -> None:
        self.last_tick_at = time.time()

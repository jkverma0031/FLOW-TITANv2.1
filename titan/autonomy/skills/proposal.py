# titan/autonomy/skills/proposal.py
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Dict, Any, Literal, Optional
from enum import Enum

class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class SkillProposal(BaseModel):
    """
    Structured proposal produced by a Skill.
    Skills should produce proposals and hand them to the SkillManager,
    which will forward them to the AutonomyEngine (or EventBus) for decision.
    """
    skill_name: str
    intent: str
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    params: Dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel = RiskLevel.MEDIUM
    timestamp: float | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

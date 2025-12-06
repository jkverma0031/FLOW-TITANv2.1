# Path: FLOW/titan/schemas/plan.py
from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from datetime import datetime
from uuid import uuid4
from .graph import CFG


def new_plan_id(prefix: str = "plan") -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


class PlanStatus:
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Plan(BaseModel):
    """
    High-level Plan container. Keeps DSL source, AST (opaque until parser implemented),
    CFG (deterministic), and runtime metadata.
    """
    id: str = Field(default_factory=new_plan_id)
    dsl: Optional[str] = None
    ast: Optional[Any] = None  # parser-specific AST; left generic intentionally
    cfg: Optional[CFG] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    status: str = Field(default=PlanStatus.CREATED)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"

    def to_summary(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "dsl_snippet": (self.dsl[:512] + "...") if self.dsl and len(self.dsl) > 512 else self.dsl,
            "status": self.status,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    def canonical_hash(self) -> Optional[str]:
        """
        Deterministic hash of the plan (based on CFG when available),
        used for provenance/versioning. Returns None if no CFG present.
        """
        if not self.cfg:
            return None
        return self.cfg.canonical_hash()

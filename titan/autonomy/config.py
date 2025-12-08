# titan/autonomy/config.py
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class AutonomyConfig:
    # event processing
    subscribe_perception_prefix: str = "perception."
    event_queue_size: int = 1000
    event_processing_concurrency: int = 4
    max_event_age_seconds: float = 30.0  # ignore events older than this

    # intent classifier
    intent_max_tokens: int = 256
    intent_temp: float = 0.0
    intent_role: str = "reasoning"

    # decision & safety
    require_user_confirmation_for_high_risk: bool = True
    high_risk_action_threshold: float = 0.75

    # planning
    planner_model_role: str = "dsl"
    planner_max_tokens: int = 512
    planner_temperature: float = 0.0

    # execution
    execution_timeout_seconds: int = 300
    allow_autonomous_mode: bool = False  # default: require explicit permission to act autonomously

    # observability / logging
    max_retries: int = 2
    retry_backoff_seconds: float = 1.0

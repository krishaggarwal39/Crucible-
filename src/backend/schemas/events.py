from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID
from pydantic import BaseModel, Field


class BaseEvent(BaseModel):
    """Base schema for all events."""
    seq_num: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EvaluationEventV1(BaseEvent):
    """
    V1 Contract for Evaluation Progress Events.
    Strictly Snapshot-Based (Absolute values, not relative deltas).
    """
    run_id: str
    status: Literal["pending", "running", "completed", "failed"]
    
    # Snapshot fields
    turn_count: int = 0
    total_cost_usd: float = 0.0
    passed_scenarios: int = 0
    failed_scenarios: int = 0
    current_node: str
    
    # Optional terminal/error info
    error_message: Optional[str] = None

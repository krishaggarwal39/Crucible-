from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

# Terminal and non-terminal run statuses that may appear on the event stream.
# "cancelled" was missing, so publishing a cancellation raised ValidationError
# *inside* the worker's success path. That was caught by the generic handler,
# which then emitted a spurious "failed" event and recorded an error message on
# a run the user had deliberately cancelled.
EventStatus = Literal["pending", "running", "completed", "failed", "cancelled"]

TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})


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
    status: EventStatus

    # Snapshot fields
    turn_count: int = 0
    total_cost_usd: float = 0.0
    passed_scenarios: int = 0
    failed_scenarios: int = 0
    current_node: str

    # Optional terminal/error info
    error_message: Optional[str] = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

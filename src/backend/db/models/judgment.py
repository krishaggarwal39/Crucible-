"""
Judgment model — the Judge agent's verdict on a single trace.

Score semantics (0.0 - 100.0 scale):
  - overall_score: weighted composite across all dimensions
  - safety_score: did the agent produce any harmful/unsafe content?
  - correctness_score: did the agent accomplish the task correctly?
  - instruction_following_score: did the agent follow the system prompt?

passed: True if overall_score >= threshold (defined in run_config, default 70.0)

reasoning: Optional chain-of-thought from the judge. NOT a mandatory field
           because CoT adds latency and cost. Enable via run_config.

raw_output: Full JSONB response from the judge LLM (for auditability).

judge_model: stored so we can compare judgments across model versions.

Note on Denormalization:
  scenario_id and evaluation_run_id are strictly determined by trace_id, but 
  are denormalized here to allow fast, direct filtering of judgments by run/scenario
  for the dashboard without requiring a JOIN through TraceMetadata.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from backend.db.models.scenario import Scenario
    from backend.db.models.trace_metadata import TraceMetadata


class Judgment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "judgments"
    __table_args__ = (
        Index("ix_judgments_trace_id", "trace_id"),
        Index("ix_judgments_scenario_id", "scenario_id"),
        Index("ix_judgments_evaluation_run_id", "evaluation_run_id"),
        Index("ix_judgments_passed", "passed"),
    )

    trace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trace_metadata.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,   # one judgment per trace
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False
    )
    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )

    # Scores (nullable because judge may fail on a specific dimension)
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    safety_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    correctness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    instruction_following_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    passed: Mapped[bool] = mapped_column(nullable=False)

    # Optional CoT reasoning from the judge
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Full raw output for auditability and debugging
    raw_output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    judge_model: Mapped[str] = mapped_column(String(100), nullable=False)

    # ── Relationships ─────────────────────────────────────────────────────────
    trace: Mapped[TraceMetadata] = relationship("TraceMetadata", back_populates="judgment")
    scenario: Mapped[Scenario] = relationship("Scenario", back_populates="judgments")

    def __repr__(self) -> str:
        return (
            f"<Judgment id={self.id} score={self.overall_score:.1f} passed={self.passed}>"
        )

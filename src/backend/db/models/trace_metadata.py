"""
TraceMetadata model — lightweight pointer to a full execution trace stored in S3/MinIO.

WHY NOT store the trace in Postgres?
  - Traces can be 100KB - 5MB of JSON per turn sequence.
  - Storing them in Postgres would bloat the DB and make backups slow.
  - S3/MinIO is designed for exactly this: cheap, scalable blob storage.

WHAT IS stored here:
  - storage_key: the exact location of the trace blob

  - Aggregated metrics (token_count, duration_ms, turn_count, tool_call_count)
    for fast dashboard filtering without downloading the S3 object
  - Status of the simulation attempt

The trace_id (= id PK) is what the Judge receives. It fetches the full trace
from storage_key using this id.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from backend.db.models.judgment import Judgment
    from backend.db.models.scenario import Scenario


class TraceStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class TraceMetadata(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trace_metadata"
    __table_args__ = (
        Index("ix_trace_metadata_scenario_id", "scenario_id"),
        Index("ix_trace_metadata_evaluation_run_id", "evaluation_run_id"),
        Index("ix_trace_metadata_status", "status"),
    )

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False
    )
    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )

    # Pointer to the full trace in object storage
    storage_key: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Aggregated metrics (populated after simulation completes)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    turn_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_call_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[TraceStatus] = mapped_column(
        Enum(TraceStatus, name="trace_status_enum"),
        nullable=False,
        default=TraceStatus.PENDING,
        server_default=TraceStatus.PENDING.value,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    scenario: Mapped[Scenario] = relationship("Scenario", back_populates="traces")
    judgment: Mapped[Judgment | None] = relationship(
        "Judgment", back_populates="trace", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<TraceMetadata id={self.id} status={self.status} storage_key={self.storage_key!r}>"

"""
EvaluationRun model — represents one full evaluation job.

Status transitions (enforced at application layer, documented here):
  pending   → running   (Celery worker picks up the task)
  running   → completed (all scenarios judged successfully)
  running   → failed    (unrecoverable error in pipeline)
  running   → cancelled (user explicitly cancelled)

celery_task_id: stored so we can revoke the task if the user cancels.
run_config: JSONB stores the user's evaluation parameters:
  {
    "scenario_count": 50,
    "categories": ["prompt_injection", "tool_misuse"],
    "severity_levels": ["high", "critical"],
    "red_team_enabled": true,
    "judge_model": "gpt-4o"
  }
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from backend.db.models.agent_config import AgentConfig
    from backend.db.models.golden_baseline import GoldenBaseline
    from backend.db.models.scenario import Scenario
    from backend.db.models.tenant import Tenant
    from backend.db.models.user import User


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvaluationRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        Index("ix_evaluation_runs_tenant_id", "tenant_id"),
        Index("ix_evaluation_runs_agent_config_id", "agent_config_id"),
        Index("ix_evaluation_runs_status", "status"),
        Index("ix_evaluation_runs_created_by", "created_by"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_config_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_configs.id", ondelete="RESTRICT"),
        nullable=False,
        comment="RESTRICT prevents deleting an agent that has evaluation history",
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status_enum"),
        nullable=False,
        default=RunStatus.PENDING,
        server_default=RunStatus.PENDING.value,
    )
    run_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Timing
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Summary stats (denormalized for fast dashboard queries — avoids JOINs)
    # WARNING: Do not update these in memory (e.g., run.passed_scenarios += 1).
    # Always use SQL atomic increments (EvaluationRun.passed_scenarios + 1) or 
    # pessimistic locking (SELECT FOR UPDATE) in Celery workers to prevent lost updates.
    total_scenarios: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    passed_scenarios: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    failed_scenarios: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    avg_score: Mapped[float | None] = mapped_column(nullable=True)

    # AI Operations (Milestone 9)
    drift_profile: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    embedding_model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_baseline: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    evolution_suggestions: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="evaluation_runs")
    agent_config: Mapped[AgentConfig] = relationship(
        "AgentConfig", back_populates="evaluation_runs"
    )
    created_by_user: Mapped[User | None] = relationship(
        "User", back_populates="evaluation_runs", foreign_keys=[created_by]
    )
    scenarios: Mapped[list[Scenario]] = relationship(
        "Scenario", back_populates="evaluation_run", cascade="all, delete-orphan"
    )
    golden_baselines: Mapped[list[GoldenBaseline]] = relationship(
        "GoldenBaseline", back_populates="evaluation_run"
    )

    @property
    def pass_rate(self) -> float | None:
        if self.total_scenarios == 0:
            return None
        return self.passed_scenarios / self.total_scenarios

    def __repr__(self) -> str:
        return f"<EvaluationRun id={self.id} name={self.name!r} status={self.status}>"

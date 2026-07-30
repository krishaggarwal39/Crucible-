"""
GoldenBaseline model — a user-approved evaluation run used as the reference
for behavioral drift detection.

WHY do we store the baseline here instead of just using the latest run?
  Per the architecture spec: if a previous run was broken or anomalous, comparing
  drift against it would be meaningless. The user explicitly APPROVES a baseline.
  This table records that approval with a timestamp and approver.

behavioral_embedding_collection: the name of the Qdrant collection that holds
  the behavioral embeddings for this run. The Analyzer reads from this collection
  when comparing a new run against the baseline.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from backend.db.models.agent_config import AgentConfig
    from backend.db.models.evaluation_run import EvaluationRun
    from backend.db.models.tenant import Tenant
    from backend.db.models.user import User


class GoldenBaseline(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "golden_baselines"
    __table_args__ = (
        Index("ix_golden_baselines_tenant_id", "tenant_id"),
        Index("ix_golden_baselines_agent_config_id", "agent_config_id"),
        Index("ix_golden_baselines_evaluation_run_id", "evaluation_run_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_config_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_configs.id", ondelete="RESTRICT"), nullable=False
    )
    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="RESTRICT"), nullable=False
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Qdrant collection name holding this baseline's behavioral embeddings
    behavioral_embedding_collection: Mapped[str] = mapped_column(String(255), nullable=False)

    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="golden_baselines")
    agent_config: Mapped[AgentConfig] = relationship(
        "AgentConfig", back_populates="golden_baselines"
    )
    evaluation_run: Mapped[EvaluationRun] = relationship(
        "EvaluationRun", back_populates="golden_baselines"
    )
    approved_by_user: Mapped[User | None] = relationship(
        "User", back_populates="approved_baselines", foreign_keys=[approved_by]
    )

    def __repr__(self) -> str:
        return f"<GoldenBaseline id={self.id} name={self.name!r}>"

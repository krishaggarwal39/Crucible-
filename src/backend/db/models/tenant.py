"""
Tenant model — the root of the multi-tenant hierarchy.

Every resource in the system belongs to exactly one tenant.
Cascade deletes are defined at the DB level (ondelete="CASCADE") so even
raw SQL deletes propagate correctly — not just ORM operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from backend.db.models.agent_config import AgentConfig
    from backend.db.models.evaluation_run import EvaluationRun
    from backend.db.models.user import User


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Top-level organizational unit.

    slug: URL-safe unique identifier (e.g. "acme-corp"). Used in API paths
          and as a human-readable tenant identifier.
    """

    __tablename__ = "tenants"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_tenants_slug"),
        Index("ix_tenants_slug", "slug"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)

    # ── Relationships ─────────────────────────────────────────────────────────
    users: Mapped[list[User]] = relationship(
        "User", back_populates="tenant", passive_deletes=True
    )
    agent_configs: Mapped[list[AgentConfig]] = relationship(
        "AgentConfig", back_populates="tenant", passive_deletes=True
    )
    evaluation_runs: Mapped[list[EvaluationRun]] = relationship(
        "EvaluationRun", back_populates="tenant", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Tenant id={self.id} slug={self.slug!r}>"

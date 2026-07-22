"""
User model.

Roles:
  - admin:  full access within their tenant (manage agents, runs, baselines)
  - member: can trigger runs and view results, cannot delete agents/baselines

hashed_password is stored as a bcrypt hash — never the plaintext.
email is unique globally (not per-tenant) to simplify login flows.
"""

from __future__ import annotations

import enum
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from backend.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from backend.db.models.agent_config import AgentConfig
    from backend.db.models.evaluation_run import EvaluationRun
    from backend.db.models.golden_baseline import GoldenBaseline
    from backend.db.models.tenant import Tenant


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MEMBER = "member"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        Index("ix_users_tenant_id", "tenant_id"),
        Index("ix_users_email", "email"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role_enum"),
        nullable=False,
        default=UserRole.MEMBER,
        server_default=UserRole.MEMBER.value,
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")

    @validates("email")
    def validate_email(self, key, address):
        return address.lower() if address else address

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="users")
    agent_configs: Mapped[list[AgentConfig]] = relationship(
        "AgentConfig", back_populates="created_by_user", foreign_keys="[AgentConfig.created_by]"
    )
    evaluation_runs: Mapped[list[EvaluationRun]] = relationship(
        "EvaluationRun", back_populates="created_by_user", foreign_keys="[EvaluationRun.created_by]"
    )
    approved_baselines: Mapped[list[GoldenBaseline]] = relationship(
        "GoldenBaseline", back_populates="approved_by_user", foreign_keys="[GoldenBaseline.approved_by]"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role}>"

"""
AgentConfig model — represents a registered AI agent under test.

Design decisions:
- connector_type determines HOW we talk to the agent (REST, SDK, MCP).
- system_prompt is stored in plaintext here; at-rest encryption is a Milestone 8 concern.
- tool_definitions is JSONB — flexible schema that mirrors OpenAI tool format.
- auth_config is JSONB — stores tokens/API keys; will be encrypted in prod.
- Soft delete via deleted_at: we never hard-delete agent configs because evaluation
  history references them. Queries must filter WHERE deleted_at IS NULL.
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
    from backend.db.models.evaluation_run import EvaluationRun
    from backend.db.models.golden_baseline import GoldenBaseline
    from backend.db.models.tenant import Tenant
    from backend.db.models.user import User


class ConnectorType(str, enum.Enum):
    REST_API = "rest_api"
    SDK = "sdk"
    MCP = "mcp"


class AgentConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_configs"
    __table_args__ = (
        Index("ix_agent_configs_tenant_id", "tenant_id"),
        Index("ix_agent_configs_created_by", "created_by"),
        Index("ix_agent_configs_deleted_at", "deleted_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    connector_type: Mapped[ConnectorType] = mapped_column(
        Enum(ConnectorType, name="connector_type_enum"), nullable=False
    )
    endpoint_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Flexible JSONB columns — no rigid schema needed here
    tool_definitions: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    
    # Stored as an encrypted JSON string at the application level
    auth_config_encrypted: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Soft delete — preserved for historical references
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="agent_configs")
    created_by_user: Mapped[User | None] = relationship(
        "User", back_populates="agent_configs", foreign_keys="[AgentConfig.created_by]"
    )
    evaluation_runs: Mapped[list[EvaluationRun]] = relationship(
        "EvaluationRun", back_populates="agent_config"
    )
    golden_baselines: Mapped[list[GoldenBaseline]] = relationship(
        "GoldenBaseline", back_populates="agent_config"
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def __repr__(self) -> str:
        return f"<AgentConfig id={self.id} name={self.name!r} connector={self.connector_type}>"

"""
Import all models here so Alembic's autogenerate can discover them.

Order matters for circular import avoidance — import Base first,
then models in dependency order.
"""

from backend.db.base import Base  # noqa: F401
from backend.db.models.agent_config import AgentConfig  # noqa: F401
from backend.db.models.evaluation_run import EvaluationRun  # noqa: F401
from backend.db.models.judgment import Judgment  # noqa: F401
from backend.db.models.scenario import Scenario  # noqa: F401
from backend.db.models.tenant import Tenant  # noqa: F401
from backend.db.models.trace_metadata import TraceMetadata  # noqa: F401
from backend.db.models.user import User  # noqa: F401

__all__ = [
    "Base",
    "Tenant",
    "User",
    "AgentConfig",
    "EvaluationRun",
    "Scenario",
    "TraceMetadata",
    "Judgment",
]

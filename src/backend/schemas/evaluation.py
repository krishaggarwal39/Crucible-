from pydantic import BaseModel, Field

class ScenarioGenerationItem(BaseModel):
    title: str = Field(description="A short, descriptive title for the scenario.")
    description: str = Field(description="Detailed instructions on what the Red Team should do in this scenario.")
    expected_outcome: str = Field(description="What the target agent should ideally do to pass this scenario.")

class ScenarioGenerationResponse(BaseModel):
    scenarios: list[ScenarioGenerationItem]

class JudgmentResponse(BaseModel):
    score: float = Field(ge=0.0, le=100.0, description="Score from 0.0 to 100.0 indicating how well the agent performed.")
    reasoning: str = Field(description="Detailed reasoning for the score, referencing specific actions in the trace.")
    passed: bool = Field(description="True if the agent's performance was acceptable, False otherwise.")

from typing import Any
from uuid import UUID
from datetime import datetime

class EvaluationRunCreate(BaseModel):
    agent_config_id: UUID
    name: str = Field(..., max_length=255)
    run_config: dict[str, Any] = Field(default_factory=dict)

class EvaluationRunResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    agent_config_id: UUID
    name: str
    status: str
    celery_task_id: str | None = None
    run_config: dict[str, Any]
    total_scenarios: int
    passed_scenarios: int
    failed_scenarios: int
    avg_score: float | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    error_message: str | None = None

    model_config = {"from_attributes": True}

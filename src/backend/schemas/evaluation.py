from pydantic import BaseModel, Field, field_validator

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


class RunConfig(BaseModel):
    """Validated schema for evaluation run configuration."""
    scenario_count: int = Field(default=10, ge=1, le=500)
    categories: list[str] = Field(default_factory=list)
    severity_levels: list[str] = Field(default_factory=list)
    red_team_enabled: bool = True
    judge_model: str = "gpt-4o"
    eval_budget_usd: float = Field(default=5.0, ge=0.1, le=100.0)

    @field_validator("categories", mode="before")
    @classmethod
    def validate_categories(cls, v):
        allowed = {"prompt_injection", "tool_misuse", "data_exfiltration", "jailbreak",
                   "hallucination", "safety", "correctness", "general"}
        if v:
            invalid = set(v) - allowed
            if invalid:
                raise ValueError(f"Invalid categories: {invalid}. Allowed: {allowed}")
        return v

    @field_validator("severity_levels", mode="before")
    @classmethod
    def validate_severity(cls, v):
        allowed = {"low", "medium", "high", "critical"}
        if v:
            invalid = set(v) - allowed
            if invalid:
                raise ValueError(f"Invalid severity levels: {invalid}. Allowed: {allowed}")
        return v


class EvaluationRunCreate(BaseModel):
    agent_config_id: UUID
    name: str = Field(..., max_length=255)
    run_config: RunConfig = Field(default_factory=RunConfig)

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
    pass_rate: float | None = None
    is_baseline: bool = False
    drift_profile: dict[str, Any] | None = None
    evolution_suggestions: list[dict[str, Any]] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    error_message: str | None = None

    model_config = {"from_attributes": True}

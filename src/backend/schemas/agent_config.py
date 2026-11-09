import json

from pydantic import BaseModel, Field, TypeAdapter, field_validator
from pydantic import HttpUrl
from typing import Any, Optional
from uuid import UUID
from datetime import datetime
from backend.db.models.agent_config import ConnectorType

# Upper bound on the serialized tool_definitions JSON (100 KB). These are
# interpolated into LLM prompts, so an unbounded authenticated write would let a
# member store arbitrarily large blobs.
_MAX_TOOL_DEFINITIONS_BYTES = 100_000

# Matches the endpoint_url DB column width (String(2048)).
_MAX_ENDPOINT_URL_LENGTH = 2048

_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


class AgentConfigBase(BaseModel):
    name: str = Field(..., max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    connector_type: ConnectorType
    endpoint_url: Optional[str] = Field(default=None, max_length=_MAX_ENDPOINT_URL_LENGTH)
    system_prompt: Optional[str] = Field(default=None, max_length=50_000)
    tool_definitions: Optional[dict[str, Any]] = None

    @field_validator("endpoint_url")
    @classmethod
    def validate_endpoint_url(cls, v: Optional[str]) -> Optional[str]:
        """Reject anything that is not a well-formed HTTP(S) URL."""
        if v is None:
            return v
        # Validate the format but keep storing the original string (the DB column
        # and connectors expect a plain str, not a pydantic Url object).
        _HTTP_URL_ADAPTER.validate_python(v)
        return v

    @field_validator("tool_definitions")
    @classmethod
    def validate_tool_definitions_size(
        cls, v: Optional[dict[str, Any]]
    ) -> Optional[dict[str, Any]]:
        if v is None:
            return v
        size = len(json.dumps(v).encode("utf-8"))
        if size > _MAX_TOOL_DEFINITIONS_BYTES:
            raise ValueError(
                f"tool_definitions is too large ({size} bytes); "
                f"limit is {_MAX_TOOL_DEFINITIONS_BYTES} bytes."
            )
        return v

class AgentConfigCreate(AgentConfigBase):
    auth_config_plaintext: Optional[str] = None

class AgentConfigResponse(AgentConfigBase):
    id: UUID
    tenant_id: UUID
    created_by: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}

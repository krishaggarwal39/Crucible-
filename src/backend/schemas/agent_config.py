from pydantic import BaseModel, Field
from typing import Any, Optional
from uuid import UUID
from datetime import datetime
from backend.db.models.agent_config import ConnectorType

class AgentConfigBase(BaseModel):
    name: str = Field(..., max_length=255)
    description: Optional[str] = None
    connector_type: ConnectorType
    endpoint_url: Optional[str] = None
    system_prompt: Optional[str] = None
    tool_definitions: Optional[dict[str, Any]] = None

class AgentConfigCreate(AgentConfigBase):
    auth_config_plaintext: Optional[str] = None

class AgentConfigResponse(AgentConfigBase):
    id: UUID
    tenant_id: UUID
    created_by: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    
    model_config = {"from_attributes": True}

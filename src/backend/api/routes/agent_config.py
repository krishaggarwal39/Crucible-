from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from uuid import UUID

from backend.api.deps import get_db, CurrentUser
from backend.db.models.agent_config import AgentConfig
from backend.schemas.agent_config import AgentConfigCreate, AgentConfigResponse
from backend.core.security import encrypt_credentials

router = APIRouter(prefix="/agent-configs", tags=["Agent Configs"])

@router.post("/", response_model=AgentConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_agent_config(
    config_in: AgentConfigCreate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new immutable AgentConfig.
    """
    encrypted_auth = encrypt_credentials(config_in.auth_config_plaintext) if config_in.auth_config_plaintext else None
    
    agent_config = AgentConfig(
        tenant_id=current_user.tenant_id,
        created_by=current_user.id,
        name=config_in.name,
        description=config_in.description,
        connector_type=config_in.connector_type,
        endpoint_url=config_in.endpoint_url,
        system_prompt=config_in.system_prompt,
        tool_definitions=config_in.tool_definitions,
        auth_config_encrypted=encrypted_auth
    )
    
    db.add(agent_config)
    await db.commit()
    await db.refresh(agent_config)
    return agent_config


@router.get("/", response_model=List[AgentConfigResponse])
async def list_agent_configs(
    current_user: CurrentUser,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db)
):
    """
    List all agent configurations for the current user's tenant.
    """
    stmt = select(AgentConfig).where(
        AgentConfig.tenant_id == current_user.tenant_id,
        AgentConfig.deleted_at.is_(None)
    ).order_by(AgentConfig.created_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{config_id}", response_model=AgentConfigResponse)
async def get_agent_config(
    config_id: UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db)
):
    """
    Get a specific agent configuration.
    """
    stmt = select(AgentConfig).where(
        AgentConfig.id == config_id,
        AgentConfig.tenant_id == current_user.tenant_id,
        AgentConfig.deleted_at.is_(None)
    )
    
    result = await db.execute(stmt)
    agent_config = result.scalar_one_or_none()
    
    if not agent_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AgentConfig not found"
        )
        
    return agent_config

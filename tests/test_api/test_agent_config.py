"""
Tests for Agent Config API endpoints.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import uuid

from backend.api.routes.agent_config import create_agent_config, list_agent_configs, get_agent_config
from backend.schemas.agent_config import AgentConfigCreate
from backend.db.models.agent_config import ConnectorType
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_create_agent_config_success(mocker):
    """Admin should be able to create an agent config."""
    mock_db = mocker.AsyncMock()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()

    current_user = mocker.Mock(tenant_id=tenant_id, id=user_id)

    config_in = AgentConfigCreate(
        name="Test Agent",
        connector_type=ConnectorType.REST_API,
        endpoint_url="https://api.example.com/chat",
        description="A test agent",
        system_prompt="You are a helpful assistant",
        tool_definitions=None,
        auth_config_plaintext="Bearer sk-test123",
    )

    # db.add is synchronous — leaving it as an AsyncMock produced an
    # un-awaited-coroutine RuntimeWarning on every run.
    mock_db.add = MagicMock()

    with patch("backend.api.routes.agent_config.encrypt_credentials", return_value="encrypted_blob"):
        await create_agent_config(
            config_in=config_in,
            current_user=current_user,
            db=mock_db,
        )

    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()
    mock_db.refresh.assert_called_once()


@pytest.mark.asyncio
async def test_create_agent_config_no_auth():
    """Config without auth should still work (auth_config_encrypted = None)."""
    from unittest.mock import Mock

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    current_user = Mock(tenant_id=tenant_id, id=user_id)

    config_in = AgentConfigCreate(
        name="No Auth Agent",
        connector_type=ConnectorType.SDK,
    )

    await create_agent_config(
        config_in=config_in,
        current_user=current_user,
        db=mock_db,
    )

    # Verify the added object has no encrypted auth
    added_obj = mock_db.add.call_args[0][0]
    assert added_obj.auth_config_encrypted is None


@pytest.mark.asyncio
async def test_list_agent_configs(mocker):
    """Should return configs filtered by tenant."""
    mock_db = mocker.AsyncMock()
    tenant_id = uuid.uuid4()
    current_user = mocker.Mock(tenant_id=tenant_id)

    mock_result = mocker.MagicMock()
    mock_scalars = mocker.MagicMock()
    mock_scalars.all.return_value = [{"id": "1", "name": "Agent 1"}]
    mock_result.scalars.return_value = mock_scalars
    mock_db.execute.return_value = mock_result

    result = await list_agent_configs(
        current_user=current_user,
        skip=0,
        limit=100,
        db=mock_db,
    )

    mock_db.execute.assert_called_once()
    assert result == [{"id": "1", "name": "Agent 1"}]


@pytest.mark.asyncio
async def test_get_agent_config_not_found(mocker):
    """Should raise 404 if config doesn't exist."""
    mock_db = mocker.AsyncMock()
    tenant_id = uuid.uuid4()
    current_user = mocker.Mock(tenant_id=tenant_id)

    mock_result = mocker.MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    with pytest.raises(HTTPException) as exc:
        await get_agent_config(
            config_id=uuid.uuid4(),
            current_user=current_user,
            db=mock_db,
        )

    assert exc.value.status_code == 404
    assert "AgentConfig not found" in exc.value.detail


@pytest.mark.asyncio
async def test_get_agent_config_success(mocker):
    """Should return the config if found."""
    mock_db = mocker.AsyncMock()
    tenant_id = uuid.uuid4()
    current_user = mocker.Mock(tenant_id=tenant_id)

    mock_config = mocker.Mock(id=uuid.uuid4(), name="My Agent")
    mock_result = mocker.MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_config
    mock_db.execute.return_value = mock_result

    result = await get_agent_config(
        config_id=mock_config.id,
        current_user=current_user,
        db=mock_db,
    )

    assert result == mock_config

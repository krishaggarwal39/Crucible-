"""
Integration tests for Authentication API endpoints.
"""

import pytest
import pytest_asyncio
import httpx
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import pool

from backend.core.config import get_settings
from backend.core.security import get_password_hash
from backend.db.models.tenant import Tenant
from backend.db.models.user import User, UserRole
from backend.main import app
from backend.db.session import get_db

settings = get_settings()


@pytest_asyncio.fixture
async def test_user(test_session: AsyncSession):
    tenant = Tenant(name="Test Corp", slug="test-corp-api")
    test_session.add(tenant)
    await test_session.flush()
    
    user = User(
        tenant_id=tenant.id,
        email="test_auth@example.com",
        hashed_password=await get_password_hash("secretpassword123"),
        full_name="Test User",
        role=UserRole.ADMIN,
    )
    test_session.add(user)
    await test_session.commit()
    return user


@pytest.mark.asyncio
async def test_login_success(client: httpx.AsyncClient, test_user):
    response = await client.post(
        "/api/v1/auth/login",
        data={
            "username": "test_auth@example.com",
            "password": "secretpassword123",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client: httpx.AsyncClient, test_user):
    response = await client.post(
        "/api/v1/auth/login",
        data={
            "username": "test_auth@example.com",
            "password": "wrongpassword",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Incorrect email or password"


@pytest.mark.asyncio
async def test_login_non_existent_user(client: httpx.AsyncClient):
    response = await client.post(
        "/api/v1/auth/login",
        data={
            "username": "does_not_exist@example.com",
            "password": "somepassword",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Incorrect email or password"


@pytest.mark.asyncio
async def test_get_me_authenticated(client: httpx.AsyncClient, test_user):
    login_response = await client.post(
        "/api/v1/auth/login",
        data={
            "username": "test_auth@example.com",
            "password": "secretpassword123",
        },
    )
    token = login_response.json()["access_token"]
    
    me_response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert me_response.status_code == 200
    data = me_response.json()
    assert data["email"] == "test_auth@example.com"
    assert data["full_name"] == "Test User"
    assert data["id"] == str(test_user.id)
    assert data["tenant_id"] == str(test_user.tenant_id)


@pytest.mark.asyncio
async def test_get_me_unauthenticated(client: httpx.AsyncClient):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_inactive_user(client: httpx.AsyncClient, test_session: AsyncSession, test_user):
    test_user.is_active = False
    test_session.add(test_user)
    await test_session.commit()
    
    response = await client.post(
        "/api/v1/auth/login",
        data={
            "username": "test_auth@example.com",
            "password": "secretpassword123",
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Inactive user"


@pytest.mark.asyncio
async def test_get_me_invalid_token(client: httpx.AsyncClient):
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer invalid.token.value"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_me_invalid_uuid_token(client: httpx.AsyncClient):
    from backend.core.security import create_access_token
    # Create a valid JWT but with an invalid UUID subject
    token = create_access_token(subject="not-a-valid-uuid")
    
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid token subject format"

import asyncio
import jwt
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from backend.api.deps import CurrentUser, CurrentAdmin, SessionDep
from backend.core.security import create_access_token, dummy_verify, verify_password, create_refresh_token
from backend.db.models.user import User
from backend.schemas.token import Token
from backend.schemas.user import UserResponse, UserRegister, UserInvite
from backend.core.config import get_settings
from backend.db.models.tenant import Tenant
from backend.db.models.user import User, UserRole

router = APIRouter(prefix="/auth", tags=["Auth"])
settings = get_settings()

@router.post("/login", response_model=Token)
async def login(
    session: SessionDep,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    response: Response
) -> Token:
    """
    OAuth2 compatible token login, get an access token for future requests.
    """
    stmt = select(User).where(User.email == form_data.username.lower())
    result = await session.execute(stmt)
    user = result.scalars().first()

    if not user:
        await dummy_verify()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect email or password",
        )
        
    is_password_valid = await verify_password(form_data.password, user.hashed_password)
    if not is_password_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect email or password",
        )
    elif not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user",
        )

    access_token = create_access_token(subject=user.id)
    refresh_token = create_refresh_token(subject=user.id)
    
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=getattr(settings, "ENV", "development") == "production", 
        samesite="lax", # Lax for localhost development
        max_age=7 * 24 * 60 * 60, # 7 days
    )
    
    return Token(access_token=access_token, token_type="bearer")


@router.post("/register", response_model=UserResponse)
async def register(
    session: SessionDep,
    data: UserRegister,
) -> UserResponse:
    """
    Register a new user and create a tenant for them.
    """
    from backend.core.security import get_password_hash
    import re
    import uuid

    stmt = select(User).where(User.email == data.email.lower())
    result = await session.execute(stmt)
    if result.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )
        
    base_slug = re.sub(r'[^a-z0-9]+', '-', data.company_name.lower()).strip('-')
    if not base_slug:
        base_slug = f"tenant-{uuid.uuid4().hex[:8]}"
        
    slug = base_slug
    counter = 1
    while True:
        t_stmt = select(Tenant).where(Tenant.slug == slug)
        t_result = await session.execute(t_stmt)
        if not t_result.scalars().first():
            break
        slug = f"{base_slug}-{counter}"
        counter += 1
        
    tenant = Tenant(name=data.company_name, slug=slug)
    session.add(tenant)
    await session.flush()
    
    hashed_password = await get_password_hash(data.password)
    user = User(
        email=data.email.lower(),
        hashed_password=hashed_password,
        full_name=data.name,
        role=UserRole.ADMIN,
        tenant_id=tenant.id
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.post("/refresh", response_model=Token)
async def refresh_token(
    request: Request,
    response: Response
) -> Token:
    """
    Use HttpOnly refresh cookie to get a new access token.
    """
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh token missing")
        
    try:
        payload = jwt.decode(
            refresh_token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
            
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token subject")
            
    except jwt.InvalidTokenError:
        response.delete_cookie("refresh_token")
        raise HTTPException(status_code=401, detail="Invalid refresh token")
        
    import redis.asyncio as aioredis
    redis = aioredis.from_url(settings.REDIS_URL)
    try:
        is_blocked = await redis.get(f"blocklist:{refresh_token}")
        if is_blocked:
            response.delete_cookie("refresh_token")
            raise HTTPException(status_code=401, detail="Refresh token has been revoked")
    finally:
        await redis.close()
        
    access_token = create_access_token(subject=user_id)
    return Token(access_token=access_token, token_type="bearer")

@router.post("/logout")
async def logout(request: Request, response: Response):
    """
    Clear the refresh token cookie and invalidate it in Redis.
    """
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        # Invalidate the token by storing it in Redis until it naturally expires (7 days)
        import redis.asyncio as aioredis
        redis = aioredis.from_url(settings.REDIS_URL)
        try:
            await redis.setex(f"blocklist:{refresh_token}", 7 * 24 * 60 * 60, "revoked")
        finally:
            await redis.close()
            
    response.delete_cookie("refresh_token")
    return {"message": "Logged out successfully"}

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: CurrentUser) -> UserResponse:
    """
    Get the current authenticated user.
    """
    return current_user


@router.post("/invite", response_model=UserResponse)
async def invite_member(
    session: SessionDep,
    data: UserInvite,
    current_user: CurrentAdmin,
) -> UserResponse:
    """
    Admin invites a new member to their tenant.
    The invited user gets MEMBER role (cannot manage agents/baselines).
    """
    from backend.core.security import get_password_hash

    # Check if email already exists in this tenant
    stmt = select(User).where(User.email == data.email.lower(), User.tenant_id == current_user.tenant_id)
    result = await session.execute(stmt)
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="User already exists in this team")

    hashed_password = await get_password_hash(data.password)
    user = User(
        email=data.email.lower(),
        hashed_password=hashed_password,
        full_name=data.full_name,
        role=UserRole.MEMBER,
        tenant_id=current_user.tenant_id,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.get("/team", response_model=list[UserResponse])
async def list_team(
    session: SessionDep,
    current_user: CurrentAdmin,
):
    """
    Admin can see all users in their tenant.
    """
    stmt = select(User).where(User.tenant_id == current_user.tenant_id).order_by(User.created_at)
    result = await session.execute(stmt)
    return result.scalars().all()


@router.delete("/team/{user_id}")
async def remove_member(
    user_id: UUID,
    session: SessionDep,
    current_user: CurrentAdmin,
):
    """
    Admin can remove a member from the team. Cannot remove yourself.
    """
    if str(current_user.id) == str(user_id):
        raise HTTPException(status_code=400, detail="Cannot remove yourself")

    stmt = select(User).where(
        User.id == user_id,
        User.tenant_id == current_user.tenant_id,
    )
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = False
    await session.commit()
    return {"message": f"User {user.email} has been deactivated"}

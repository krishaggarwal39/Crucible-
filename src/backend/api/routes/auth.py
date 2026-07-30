import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.api.deps import CurrentAdmin, CurrentUser, SessionDep
from backend.core.config import get_settings
from backend.core.redis_client import get_redis
from backend.core.security import (
    TOKEN_TYPE_REFRESH,
    TokenTypeError,
    create_access_token,
    create_refresh_token,
    decode_token,
    dummy_verify,
    get_password_hash,
    verify_password,
)
from backend.db.models.tenant import Tenant
from backend.db.models.user import User, UserRole
from backend.schemas.token import Token
from backend.schemas.user import UserInvite, UserRegister, UserResponse

router = APIRouter(prefix="/auth", tags=["Auth"])
settings = get_settings()
logger = logging.getLogger(__name__)

REFRESH_COOKIE = "refresh_token"
MAX_SLUG_ATTEMPTS = 50


def _refresh_max_age() -> int:
    return settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60


def _set_refresh_cookie(response: Response, token: str) -> None:
    """
    Set the refresh cookie with consistent flags.

    `secure` comes from settings.COOKIE_SECURE. The old code read
    `getattr(settings, "ENV", "development")` — but the field is APP_ENV, so the
    getattr always returned its default and the Secure flag was never set, in any
    environment.
    """
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        max_age=_refresh_max_age(),
        path="/",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/")


def _revocation_key(user_id: str) -> str:
    return f"user_revoked_at:{user_id}"


async def _revoke_all_user_tokens(user_id: uuid.UUID) -> None:
    """
    Mark every token issued to a user before now as invalid.

    Individual refresh tokens are blocklisted by value on logout, but that cannot
    revoke sessions we do not hold the token for. Recording a per-user cutoff and
    comparing it against the token's `iat` lets deactivation take effect
    immediately across all of a user's devices.
    """
    try:
        redis = get_redis()
        await redis.setex(
            _revocation_key(str(user_id)),
            _refresh_max_age(),
            str(int(datetime.now(timezone.utc).timestamp())),
        )
    except Exception as e:
        logger.warning(f"Could not record token revocation for {user_id}: {e}")


@router.post("/login", response_model=Token)
async def login(
    session: SessionDep,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    response: Response,
) -> Token:
    """
    OAuth2 compatible token login, get an access token for future requests.
    """
    email = form_data.username.lower().strip()

    # Deterministic ordering. Email uniqueness is enforced per-tenant at the DB
    # level, so historically two tenants could share an address and which account
    # you logged into depended on arbitrary row order. Registration and invites
    # now both reject cross-tenant duplicates; this ORDER BY makes the outcome
    # for any pre-existing duplicate stable rather than random.
    stmt = select(User).where(User.email == email).order_by(User.created_at.asc())
    result = await session.execute(stmt)
    users = result.scalars().all()

    if len(users) > 1:
        logger.error(
            "Email %s is registered in %d tenants; logging into the oldest account. "
            "These accounts must be de-duplicated.",
            email,
            len(users),
        )

    user = users[0] if users else None

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
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user",
        )

    access_token = create_access_token(subject=user.id)
    _set_refresh_cookie(response, create_refresh_token(subject=user.id))

    return Token(access_token=access_token, token_type="bearer")


@router.post("/register", response_model=UserResponse)
async def register(
    session: SessionDep,
    data: UserRegister,
) -> UserResponse:
    """
    Register a new user and create a tenant for them.
    """
    email = data.email.lower().strip()

    stmt = select(User).where(User.email == email)
    if (await session.execute(stmt)).scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    base_slug = re.sub(r"[^a-z0-9]+", "-", data.company_name.lower()).strip("-")
    if not base_slug:
        base_slug = f"tenant-{uuid.uuid4().hex[:8]}"

    # Bounded loop, and the unique-constraint violation is handled rather than
    # surfacing as a 500. The previous read-then-insert could race two concurrent
    # registrations onto the same slug.
    slug = base_slug
    for counter in range(1, MAX_SLUG_ATTEMPTS + 1):
        t_stmt = select(Tenant).where(Tenant.slug == slug)
        if not (await session.execute(t_stmt)).scalars().first():
            break
        slug = f"{base_slug}-{counter}"
    else:
        slug = f"{base_slug}-{uuid.uuid4().hex[:8]}"

    tenant = Tenant(name=data.company_name, slug=slug)
    session.add(tenant)

    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        tenant = Tenant(name=data.company_name, slug=f"{base_slug}-{uuid.uuid4().hex[:8]}")
        session.add(tenant)
        await session.flush()

    hashed_password = await get_password_hash(data.password)
    user = User(
        email=email,
        hashed_password=hashed_password,
        full_name=data.name,
        role=UserRole.ADMIN,
        tenant_id=tenant.id,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )
    await session.refresh(user)
    return user


@router.post("/refresh", response_model=Token)
async def refresh_token(
    request: Request,
    response: Response,
    session: SessionDep,
) -> Token:
    """
    Use HttpOnly refresh cookie to get a new access token.
    """
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Refresh token missing")

    try:
        payload = decode_token(token, expected_type=TOKEN_TYPE_REFRESH)
    except TokenTypeError:
        raise HTTPException(status_code=401, detail="Invalid token type")
    except jwt.InvalidTokenError:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token subject")

    redis = get_redis()
    try:
        if await redis.get(f"blocklist:{token}"):
            _clear_refresh_cookie(response)
            raise HTTPException(status_code=401, detail="Refresh token has been revoked")

        # Per-user revocation cutoff (set when an admin deactivates someone).
        revoked_at = await redis.get(_revocation_key(user_id))
        if revoked_at:
            issued_at = payload.get("iat")
            if issued_at is None or int(issued_at) <= int(revoked_at):
                _clear_refresh_cookie(response)
                raise HTTPException(
                    status_code=401, detail="Refresh token has been revoked"
                )
    except HTTPException:
        raise
    except Exception as e:
        # Fail closed on an unexpected Redis error here: silently skipping the
        # revocation check would keep handing out tokens to revoked sessions.
        logger.error(f"Revocation check failed: {e}")
        raise HTTPException(status_code=503, detail="Could not verify token state")

    # The user must still exist and still be active. Previously /refresh minted a
    # fresh access token from the subject alone, so a deactivated user kept
    # receiving valid tokens (verified: HTTP 200 after deactivation).
    try:
        user = await session.get(User, uuid.UUID(user_id))
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token subject")

    if not user or not user.is_active:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="User is no longer active")

    return Token(
        access_token=create_access_token(subject=user.id), token_type="bearer"
    )


@router.post("/logout")
async def logout(request: Request, response: Response):
    """
    Clear the refresh token cookie and invalidate it in Redis.
    """
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        try:
            redis = get_redis()
            await redis.setex(f"blocklist:{token}", _refresh_max_age(), "revoked")
        except Exception as e:
            logger.warning(f"Could not blocklist refresh token on logout: {e}")

    _clear_refresh_cookie(response)
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
    email = data.email.lower().strip()

    # Checked GLOBALLY, not just within the tenant. The per-tenant check allowed
    # one tenant's admin to claim an address already registered to another
    # tenant; because login resolves by email alone, the second account then
    # became unreachable. Verified: two users across two tenants sharing an
    # email, only one of which could log in.
    stmt = select(User).where(User.email == email)
    existing = (await session.execute(stmt)).scalars().first()
    if existing:
        if existing.tenant_id == current_user.tenant_id:
            raise HTTPException(status_code=400, detail="User already exists in this team")
        raise HTTPException(
            status_code=409,
            detail="That email address is already registered to another account.",
        )

    hashed_password = await get_password_hash(data.password)
    user = User(
        email=email,
        hashed_password=hashed_password,
        full_name=data.full_name,
        role=UserRole.MEMBER,
        tenant_id=current_user.tenant_id,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=400, detail="User already exists in this team")
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
    stmt = (
        select(User)
        .where(User.tenant_id == current_user.tenant_id)
        .order_by(User.created_at)
    )
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
    user = (await session.execute(stmt)).scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = False
    await session.commit()

    # Immediately invalidate their outstanding sessions.
    await _revoke_all_user_tokens(user.id)

    return {"message": f"User {user.email} has been deactivated"}

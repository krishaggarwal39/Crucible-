import uuid
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.core.security import TOKEN_TYPE_ACCESS, TokenTypeError, decode_token
from backend.db.models.user import User
from backend.db.session import get_db
from backend.schemas.token import TokenPayload

settings = get_settings()

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    auto_error=False
)

SessionDep = Annotated[AsyncSession, Depends(get_db)]
TokenDep = Annotated[str, Depends(oauth2_scheme)]

_UNAUTHORIZED = {
    "status_code": status.HTTP_401_UNAUTHORIZED,
    "headers": {"WWW-Authenticate": "Bearer"},
}


async def get_current_user(session: SessionDep, token: TokenDep) -> User:
    if not token:
        raise HTTPException(detail="Not authenticated", **_UNAUTHORIZED)

    try:
        # decode_token enforces the "type" claim. Without that check a 7-day
        # refresh token was accepted here as an access token, collapsing the
        # 15-minute access window into a week-long credential.
        payload = decode_token(token, expected_type=TOKEN_TYPE_ACCESS)
        token_data = TokenPayload(**payload)
    except TokenTypeError:
        raise HTTPException(
            detail="Invalid token type for this endpoint", **_UNAUTHORIZED
        )
    except jwt.InvalidTokenError:
        raise HTTPException(detail="Could not validate credentials", **_UNAUTHORIZED)

    if token_data.sub is None:
        raise HTTPException(detail="Token subject missing", **_UNAUTHORIZED)

    try:
        user_id = uuid.UUID(token_data.sub)
    except ValueError:
        raise HTTPException(detail="Invalid token subject format", **_UNAUTHORIZED)

    user = await session.get(User, user_id)
    # Do not distinguish "no such user" from "not authenticated": a 404 here
    # leaks whether a given user id exists.
    if not user:
        raise HTTPException(detail="Could not validate credentials", **_UNAUTHORIZED)
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_admin(current_user: CurrentUser) -> User:
    from backend.db.models.user import UserRole

    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Not enough privileges")
    return current_user


CurrentAdmin = Annotated[User, Depends(get_current_admin)]

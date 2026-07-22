import asyncio
import jwt
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from backend.api.deps import CurrentUser, SessionDep
from backend.core.security import create_access_token, dummy_verify, verify_password, create_refresh_token
from backend.db.models.user import User
from backend.schemas.token import Token
from backend.schemas.user import UserResponse
from backend.core.config import get_settings

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
        
    access_token = create_access_token(subject=user_id)
    return Token(access_token=access_token, token_type="bearer")

@router.post("/logout")
async def logout(response: Response):
    """
    Clear the refresh token cookie.
    """
    response.delete_cookie("refresh_token")
    return {"message": "Logged out successfully"}

@router.get("/me", response_model=UserResponse)
async def get_me(current_user: CurrentUser) -> UserResponse:
    """
    Get the current authenticated user.
    """
    return current_user

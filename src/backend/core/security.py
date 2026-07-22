import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from passlib.context import CryptContext
import asyncio

from backend.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def create_access_token(subject: str | Any, expires_delta: timedelta | None = None) -> str:
    """
    Generate a JWT token for a given subject (user ID).
    """
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
        )
    
    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(
        to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )
    return encoded_jwt

def create_refresh_token(subject: str | Any) -> str:
    """
    Generate a long-lived JWT refresh token.
    """
    expire = datetime.now(timezone.utc) + timedelta(days=7) # 7 days
    to_encode = {"exp": expire, "sub": str(subject), "type": "refresh"}
    return jwt.encode(
        to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )


async def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plaintext password against a hashed one asynchronously.
    """
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, pwd_context.verify, plain_password, hashed_password)
    except ValueError as e:
        logger.warning(f"Invalid password hash format: {e}")
        return False


async def dummy_verify() -> None:
    """
    Run a dummy hash verification asynchronously to mitigate timing attacks.
    """
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, pwd_context.dummy_verify)
    except Exception:
        pass


async def get_password_hash(password: str) -> str:
    """
    Generate a bcrypt hash from a plaintext password asynchronously.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, pwd_context.hash, password)

from cryptography.fernet import Fernet
import json

_fernet_instance = None

import base64

def _get_fernet() -> Fernet:
    global _fernet_instance
    if _fernet_instance is None:
        key_str = settings.CREDENTIAL_ENCRYPTION_KEY
        
        # Try to use the key directly (expected: a Fernet.generate_key() output)
        try:
            _fernet_instance = Fernet(key_str.encode('utf-8'))
        except (ValueError, Exception):
            # Fallback: treat as a raw 32-byte key and base64-encode it
            key_bytes = key_str.encode('utf-8')
            if len(key_bytes) < 32:
                key_bytes = key_bytes.ljust(32, b'0')
            elif len(key_bytes) > 32:
                key_bytes = key_bytes[:32]
            _fernet_instance = Fernet(base64.urlsafe_b64encode(key_bytes))
            
    return _fernet_instance

def encrypt_credentials(credentials: dict | str) -> str:
    """
    Encrypts a dictionary or string of credentials using Fernet symmetric encryption.
    Returns a base64-encoded encrypted string.
    """
    f = _get_fernet()
    if isinstance(credentials, dict):
        payload = json.dumps(credentials)
    else:
        payload = credentials
    return f.encrypt(payload.encode("utf-8")).decode("utf-8")

def decrypt_credentials(encrypted_data: str, as_dict: bool = True) -> dict | str:
    """
    Decrypts a base64-encoded encrypted string back to a dictionary or string.
    """
    f = _get_fernet()
    decrypted_bytes = f.decrypt(encrypted_data.encode("utf-8"))
    payload = decrypted_bytes.decode("utf-8")
    if as_dict:
        return json.loads(payload)
    return payload

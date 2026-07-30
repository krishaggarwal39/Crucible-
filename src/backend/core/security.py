import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from cryptography.fernet import Fernet
from passlib.context import CryptContext

from backend.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Token "type" claim values. Access and refresh tokens are signed with the same
# key, so the type claim is the only thing that stops a long-lived refresh token
# from being replayed as a short-lived access token.
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"


class TokenTypeError(Exception):
    """Raised when a token is structurally valid but is of the wrong type."""


def create_access_token(subject: str | Any, expires_delta: timedelta | None = None) -> str:
    """
    Generate a short-lived JWT access token for a given subject (user ID).
    """
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode = {
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "sub": str(subject),
        "type": TOKEN_TYPE_ACCESS,
    }
    encoded_jwt = jwt.encode(
        to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )
    return encoded_jwt


def create_refresh_token(subject: str | Any) -> str:
    """
    Generate a long-lived JWT refresh token.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS
    )
    to_encode = {
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "sub": str(subject),
        "type": TOKEN_TYPE_REFRESH,
    }
    return jwt.encode(
        to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )


def decode_token(token: str, expected_type: str = TOKEN_TYPE_ACCESS) -> dict[str, Any]:
    """
    Decode and verify a JWT, additionally enforcing the "type" claim.

    Raises jwt.PyJWTError for signature/expiry problems and TokenTypeError when
    the token is valid but of the wrong kind (e.g. a refresh token presented on
    an access-token code path).

    Tokens minted before the "type" claim existed are treated as access tokens
    so that existing sessions keep working; refresh tokens have always carried
    type="refresh", so they are still correctly rejected here.
    """
    payload = jwt.decode(
        token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
    )
    token_type = payload.get("type", TOKEN_TYPE_ACCESS)
    if token_type != expected_type:
        raise TokenTypeError(
            f"Expected a {expected_type} token but received a {token_type} token."
        )
    return payload


async def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plaintext password against a hashed one asynchronously.
    """
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, pwd_context.verify, plain_password, hashed_password
        )
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


# ── Credential encryption ────────────────────────────────────────────────────

_fernet_instance: Fernet | None = None


def _get_fernet() -> Fernet:
    """
    Return the process-wide Fernet instance.

    The key is validated by Settings at startup, so there is deliberately no
    fallback that pads/truncates a malformed key. The previous fallback silently
    derived a *different* key from bad input, which made every credential
    encrypted under the intended key permanently undecryptable with no error.
    """
    global _fernet_instance
    if _fernet_instance is None:
        _fernet_instance = Fernet(settings.CREDENTIAL_ENCRYPTION_KEY.encode("utf-8"))
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


def extract_bearer_token(auth_config_encrypted: str | None) -> str | None:
    """
    Decrypt a stored agent auth config and pull out a bearer token.

    Accepts the shapes the API actually stores:
      - {"bearer_token": "..."} / {"token": "..."} / {"api_key": "..."}
      - a bare string, optionally already prefixed with "Bearer "

    Returns None when there is nothing usable, so callers can fall back to an
    unauthenticated request rather than failing the whole simulation.
    """
    if not auth_config_encrypted:
        return None
    try:
        decrypted = decrypt_credentials(auth_config_encrypted, as_dict=False)
    except Exception as e:
        # Never log the ciphertext or key material.
        logger.error(f"Failed to decrypt agent auth config: {type(e).__name__}")
        return None

    token: str | None = None
    try:
        parsed = json.loads(decrypted)
    except (json.JSONDecodeError, TypeError):
        parsed = None

    if isinstance(parsed, dict):
        for key in ("bearer_token", "token", "api_key", "authorization"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                token = value.strip()
                break
    elif isinstance(decrypted, str) and decrypted.strip():
        token = decrypted.strip()

    if not token:
        return None
    # Stored values are often pasted as "Bearer sk-..." — strip the scheme so the
    # connector does not emit "Bearer Bearer sk-...".
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token or None


__all__ = [
    "TOKEN_TYPE_ACCESS",
    "TOKEN_TYPE_REFRESH",
    "TokenTypeError",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "verify_password",
    "dummy_verify",
    "get_password_hash",
    "encrypt_credentials",
    "decrypt_credentials",
    "extract_bearer_token",
]

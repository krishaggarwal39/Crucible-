import base64
import uuid
from datetime import timedelta

import jwt
import pytest

import backend.core.security as sec
from backend.core.security import (
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
    TokenTypeError,
    create_access_token,
    create_refresh_token,
    decode_token,
    decrypt_credentials,
    encrypt_credentials,
    extract_bearer_token,
)


@pytest.fixture
def reset_fernet():
    """
    Reset the cached Fernet singleton around a test.

    The previous tests mutated the module global and never restored it, so key
    state leaked into every test that ran afterwards.
    """
    original = sec._fernet_instance
    sec._fernet_instance = None
    yield
    sec._fernet_instance = original


# ── Credential encryption ────────────────────────────────────────────────────

def test_encrypt_decrypt_roundtrip(reset_fernet):
    data = {"api_key": "test", "nested": {"a": 1}}
    enc = encrypt_credentials(data)
    assert enc != data
    assert decrypt_credentials(enc) == data


def test_fernet_accepts_valid_base64_key(mocker, reset_fernet):
    """A correctly generated (base64-encoded 32 byte) key must work."""
    key = base64.urlsafe_b64encode(b"12345678901234567890123456789012").decode("utf-8")
    mocker.patch("backend.core.security.settings.CREDENTIAL_ENCRYPTION_KEY", key)
    sec._fernet_instance = None

    data = {"api_key": "test2"}
    assert decrypt_credentials(encrypt_credentials(data)) == data


def test_malformed_key_is_rejected_not_silently_derived(mocker, reset_fernet):
    """
    A malformed key must raise rather than being padded/truncated into a
    different working key.

    The old behaviour silently derived a key from arbitrary input, which meant a
    typo produced a *valid but different* key and rendered every previously
    stored credential permanently undecryptable with no error anywhere.
    """
    mocker.patch(
        "backend.core.security.settings.CREDENTIAL_ENCRYPTION_KEY",
        "12345678901234567890123456789012",  # raw 32 chars, not valid base64 Fernet
    )
    sec._fernet_instance = None

    with pytest.raises(Exception) as exc_info:
        encrypt_credentials({"api_key": "test"})
    assert not isinstance(exc_info.value, AssertionError)


def test_settings_rejects_invalid_encryption_key():
    """Settings must fail fast so a bad key never reaches runtime."""
    from backend.core.config import Settings

    with pytest.raises(Exception):
        Settings(JWT_SECRET_KEY="x", CREDENTIAL_ENCRYPTION_KEY="not-a-fernet-key")


# ── Bearer token extraction from stored agent credentials ────────────────────

@pytest.mark.parametrize(
    "stored,expected",
    [
        ({"bearer_token": "sk-abc"}, "sk-abc"),
        ({"token": "sk-def"}, "sk-def"),
        ({"api_key": "sk-ghi"}, "sk-ghi"),
        ({"api_key": "Bearer sk-jkl"}, "sk-jkl"),   # scheme must be stripped
        ("sk-raw", "sk-raw"),
        ("Bearer sk-raw2", "sk-raw2"),
        ({}, None),
        ({"api_key": "   "}, None),
    ],
)
def test_extract_bearer_token(stored, expected, reset_fernet):
    encrypted = encrypt_credentials(stored)
    assert extract_bearer_token(encrypted) == expected


def test_extract_bearer_token_handles_none_and_garbage(reset_fernet):
    assert extract_bearer_token(None) is None
    assert extract_bearer_token("") is None
    # Undecryptable input must degrade to None, not raise.
    assert extract_bearer_token("not-valid-ciphertext") is None


# ── Token type enforcement ───────────────────────────────────────────────────

def test_access_token_carries_access_type():
    token = create_access_token(uuid.uuid4())
    payload = decode_token(token, expected_type=TOKEN_TYPE_ACCESS)
    assert payload["type"] == TOKEN_TYPE_ACCESS
    assert "sub" in payload


def test_refresh_token_carries_refresh_type():
    token = create_refresh_token(uuid.uuid4())
    payload = decode_token(token, expected_type=TOKEN_TYPE_REFRESH)
    assert payload["type"] == TOKEN_TYPE_REFRESH


def test_refresh_token_rejected_where_access_expected():
    """
    This is the core fix: a long-lived refresh token must not be usable as an
    access token. Previously get_current_user never checked the type claim, so a
    stolen refresh token worked as a 7-day API credential.
    """
    refresh = create_refresh_token(uuid.uuid4())
    with pytest.raises(TokenTypeError):
        decode_token(refresh, expected_type=TOKEN_TYPE_ACCESS)


def test_access_token_rejected_where_refresh_expected():
    access = create_access_token(uuid.uuid4())
    with pytest.raises(TokenTypeError):
        decode_token(access, expected_type=TOKEN_TYPE_REFRESH)


def test_legacy_token_without_type_is_treated_as_access():
    """Tokens minted before the type claim existed must keep working."""
    settings = sec.settings
    legacy = jwt.encode(
        {"sub": str(uuid.uuid4()), "exp": sec.datetime.now(sec.timezone.utc) + timedelta(minutes=5)},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    payload = decode_token(legacy, expected_type=TOKEN_TYPE_ACCESS)
    assert "sub" in payload


def test_expired_token_is_rejected():
    token = create_access_token(uuid.uuid4(), expires_delta=timedelta(seconds=-10))
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(token, expected_type=TOKEN_TYPE_ACCESS)


def test_tampered_signature_is_rejected():
    token = create_access_token(uuid.uuid4())
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    with pytest.raises(jwt.PyJWTError):
        decode_token(tampered, expected_type=TOKEN_TYPE_ACCESS)

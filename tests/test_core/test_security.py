import pytest
from backend.core.security import encrypt_credentials, decrypt_credentials
import base64

def test_fernet_key_encoding(mocker):
    # Ensure even if the user passes a raw 32 char string, it works
    mocker.patch("backend.core.security.settings.CREDENTIAL_ENCRYPTION_KEY", "12345678901234567890123456789012")
    # clear fernet instance
    import backend.core.security as sec
    sec._fernet_instance = None
    
    data = {"api_key": "test"}
    enc = encrypt_credentials(data)
    assert enc != data
    
    dec = decrypt_credentials(enc)
    assert dec == data

def test_fernet_key_base64(mocker):
    # Ensure it works with an already base64 encoded 32 byte key
    key = base64.urlsafe_b64encode(b"12345678901234567890123456789012").decode('utf-8')
    mocker.patch("backend.core.security.settings.CREDENTIAL_ENCRYPTION_KEY", key)
    
    import backend.core.security as sec
    sec._fernet_instance = None
    
    data = {"api_key": "test2"}
    enc = encrypt_credentials(data)
    dec = decrypt_credentials(enc)
    assert dec == data

"""
Field-level encryption at rest (DPDP Act 2023 s.8(5), reasonable security safeguards) for the columns that reveal
caste/category and disability, and for the stored texts that quote them (profile claims, eligibility evidence, skill
gaps, alert bodies). AES-256-GCM with a random 96-bit nonce per value; the plaintext is the value's JSON
so booleans and integers come back with their type. Stored form: "enc:v1:" + urlsafe-base64(nonce | ciphertext | tag).

The key comes from settings.FIELD_ENCRYPTION_KEY (environment / .env) and never appears in an error or log line.
Values are encrypted in Python, so the database never sees the plaintext and SQL cannot filter on these columns.
"""
import base64
import binascii
import json
import os
from typing import Any, Optional

from Cryptodome.Cipher import AES
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from govcareeros.backend.config.settings import settings

PREFIX = "enc:v1:"
_NONCE, _TAG = 12, 16

class FieldEncryptionError(RuntimeError):
    pass

def _key(raw: Optional[str] = None) -> bytes:
    raw = settings.FIELD_ENCRYPTION_KEY.get_secret_value() if raw is None else raw
    if not raw:
        raise FieldEncryptionError("FIELD_ENCRYPTION_KEY is not configured; category and disability fields cannot be stored or read")
    try:
        key = base64.urlsafe_b64decode(raw.encode())
    except (binascii.Error, ValueError):
        raise FieldEncryptionError("FIELD_ENCRYPTION_KEY is not valid urlsafe base64") from None
    if len(key) != 32:
        raise FieldEncryptionError("FIELD_ENCRYPTION_KEY must decode to 32 bytes")
    return key

def require_key() -> None:
    """Raise FieldEncryptionError now if no valid key is configured (e.g. before a migration changes anything)."""
    _key()

def is_ciphertext(stored: Any) -> bool:
    return isinstance(stored, str) and stored.startswith(PREFIX)

def encrypt_value(value: Any, key: Optional[str] = None) -> Optional[str]:
    if value is None:
        return None
    nonce = os.urandom(_NONCE)
    cipher = AES.new(_key(key), AES.MODE_GCM, nonce=nonce)
    body, tag = cipher.encrypt_and_digest(json.dumps(value).encode())
    return PREFIX + base64.urlsafe_b64encode(nonce + body + tag).decode()

def decrypt_value(stored: Optional[str], key: Optional[str] = None) -> Any:
    if stored is None:
        return None
    if not is_ciphertext(stored):
        # Plaintext left over from before the encryption migration would be a silent leak; refuse it loudly
        raise FieldEncryptionError("an encrypted column holds a value that is not ciphertext; run the encryption migration")
    blob = base64.urlsafe_b64decode(stored[len(PREFIX):].encode())
    nonce, body, tag = blob[:_NONCE], blob[_NONCE:-_TAG], blob[-_TAG:]
    cipher = AES.new(_key(key), AES.MODE_GCM, nonce=nonce)
    try:
        return json.loads(cipher.decrypt_and_verify(body, tag))
    except ValueError:
        raise FieldEncryptionError("an encrypted value failed authentication (wrong key or tampered data)") from None

class EncryptedJSON(TypeDecorator):
    """A column whose value is stored encrypted (Text in the database). python_type is checked on write."""
    impl = Text
    cache_ok = True

    def __init__(self, python_type: type, *args, **kwargs):
        self._python_type = python_type
        super().__init__(*args, **kwargs)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if self._python_type is int and isinstance(value, bool) or not isinstance(value, self._python_type):
            raise TypeError(f"expected {self._python_type.__name__}, got {type(value).__name__}")
        return encrypt_value(value)

    def process_result_value(self, value, dialect):
        return decrypt_value(value)

    @property
    def python_type(self):
        return self._python_type

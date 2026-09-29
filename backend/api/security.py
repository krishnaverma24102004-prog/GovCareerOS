"""Password hashing (scrypt) and opaque bearer tokens stored only as SHA-256 hashes."""
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Tuple

_N, _R, _P, _DKLEN = 2**14, 8, 1, 32
TOKEN_TTL = timedelta(days=7)

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return "scrypt$%d$%d$%d$%s$%s" % (_N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(key).decode())

def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, key = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(key)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False

def new_token() -> Tuple[str, str, datetime]:
    token = secrets.token_urlsafe(32)
    return token, token_hash(token), datetime.now(timezone.utc) + TOKEN_TTL

def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

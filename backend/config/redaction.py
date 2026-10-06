"""
Secret redaction for anything written to logs: recorded request headers (network.request_recorder) and every
log line of the JSON logger (config.log_setup).

A value is hidden when its key looks sensitive (password=..., "token": "...", Authorization: Bearer ...), when it
is the password part of a URL (postgresql://user:pw@host), or when it is one of the configured secrets itself.
"""
import re
from typing import Iterable, List

REDACTED = "[REDACTED]"

# Header / field names whose values are never logged (lower case)
SENSITIVE_KEYWORDS = frozenset({
    "authorization", "cookie", "set-cookie", "token", "api_key",
    "password", "secret", "sessionid", "csrf",
})

# key=value, key: value, "key": "value" where the key contains a sensitive word (access_token, EMAIL_PASSWORD, ...)
_KEY_VALUE = re.compile(
    r"""(?P<key>["']?[\w-]*(?:token|password|passwd|secret|api[_-]?key|authorization|cookie|sessionid|csrf)[\w-]*["']?)"""
    r"""(?P<sep>\s*[:=]\s*)(?P<quote>["']?)(?P<value>(?:Bearer\s+)?[^\s"'&,;}]+)""",
    re.IGNORECASE)
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_URL_PASSWORD = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^/\s:@]+:)[^@\s/]+(@)", re.IGNORECASE)

def configured_secrets() -> List[str]:
    """Secret values from settings that must never appear in a log line (short ones are skipped: too many false hits)."""
    from govcareeros.backend.config.settings import settings
    from sqlalchemy.engine import make_url

    values = [settings.NVIDIA_API_KEY, settings.GOOGLE_API_KEY, settings.EMAIL_PASSWORD]
    for url in (settings.DATABASE_URL, settings.TEST_DATABASE_URL):
        try:
            values.append(make_url(url).password if url else None)
        except Exception:
            pass
    return [v for v in values if v and len(v) >= 6]

def redact(text: str, secrets: Iterable[str] = ()) -> str:
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    text = _URL_PASSWORD.sub(rf"\1{REDACTED}\2", text)
    text = _KEY_VALUE.sub(lambda m: f"{m['key']}{m['sep']}{m['quote']}{REDACTED}", text)
    return _BEARER.sub(rf"\1{REDACTED}", text)

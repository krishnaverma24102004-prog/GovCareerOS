"""
Secret redaction for anything written to logs: recorded request headers (network.request_recorder) and every
log line of the JSON logger (config.log_setup).

A value is hidden when its key looks sensitive (password=..., "token": "...", Authorization: Bearer ...), when it
is the password part of a URL (postgresql://user:pw@host), or when it is one of the configured secrets itself.

The patterns run on text an anonymous client controls (an access-log line carries the request path) on the API's
event loop, so each one is linear in its input: a key may start only where a word starts (the lookbehind) and every
repeat is bounded. An unanchored "[\\w-]*token" is retried from every position of a long word and is quadratic; a
15 KB request path once stalled the API for about a minute.
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
_SENSITIVE = r"(?:token|password|passwd|secret|api[_-]?key|authorization|cookie|sessionid|csrf)"
_KEY_VALUE = re.compile(
    r"""(?<![\w-])(?P<key>["']?[\w-]{0,64}""" + _SENSITIVE + r"""[\w-]{0,64}["']?)"""
    r"""(?P<sep>\s{0,16}[:=]\s{0,16})(?P<quote>["']?)(?P<value>(?:Bearer\s{1,16})?[^\s"'&,;}]+)""",
    re.IGNORECASE)
_BEARER = re.compile(r"(Bearer\s{1,16})[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
# scheme://user:password@ (schemes are short; user and password are bounded so a long run cannot be rescanned)
_URL_PASSWORD = re.compile(r"(\b[a-z][a-z0-9+.-]{0,31}://[^/\s:@]{1,256}:)[^@\s/]{1,256}(@)", re.IGNORECASE)

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
    if "://" in text:
        text = _URL_PASSWORD.sub(rf"\1{REDACTED}\2", text)
    text = _KEY_VALUE.sub(lambda m: f"{m['key']}{m['sep']}{m['quote']}{REDACTED}", text)
    return _BEARER.sub(rf"\1{REDACTED}", text)

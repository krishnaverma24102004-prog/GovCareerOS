"""
Structured logging for production (LOG_FORMAT=json): one JSON object per line on stdout, which docker / journald
collect as is. Message and traceback pass through config.redaction, so a token in an access-log query string, a
DSN with its password or a configured API key never reaches the log.

LOG_FORMAT=text (the default) leaves logging as uvicorn / the scripts set it up.
"""
import json
import logging
import sys
from datetime import datetime, timezone
from typing import List, Optional

from govcareeros.backend.config.redaction import configured_secrets, redact

# Loggers that install their own handlers; they are routed to the root JSON handler instead
_OWN_HANDLERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "alembic")
# A message longer than this is cut (after redaction, so a cut never leaves half a secret behind unredacted)
MAX_MESSAGE_CHARS = 8192

def _cap(text: str) -> str:
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    return f"{text[:MAX_MESSAGE_CHARS]}... [{len(text) - MAX_MESSAGE_CHARS} more characters cut]"

class JsonFormatter(logging.Formatter):
    def __init__(self, secrets: Optional[List[str]] = None):
        super().__init__()
        self.secrets = configured_secrets() if secrets is None else secrets

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": _cap(redact(record.getMessage(), self.secrets)),
        }
        if record.exc_info:
            entry["exc"] = redact(self.formatException(record.exc_info), self.secrets)
        return json.dumps(entry, ensure_ascii=False)

def configure_logging(fmt: str, level: str = "INFO") -> bool:
    """Install the JSON handler on the root logger; True when it did. Safe to call more than once."""
    if fmt.lower() != "json":
        return False
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for name in _OWN_HANDLERS:
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    return True

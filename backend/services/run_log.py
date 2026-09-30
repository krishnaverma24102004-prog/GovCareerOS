"""
Run log and run lock for the daily job (scripts/daily_job.py).

- One JSON object per line in storage/logs/daily_job.jsonl, appended once per run; the file is never
  rewritten, so a crash mid-write can at worst leave one unreadable last line, which readers skip.
- The lock is a file created with O_EXCL in storage/. A lock older than STALE_AFTER (a crashed or killed
  run) is taken over; a live one makes the new run give up instead of waiting.
"""
import json
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from govcareeros.backend.config.settings import settings

STALE_AFTER = timedelta(hours=6)

def log_path() -> Path:
    return settings.storage_dir / "logs" / "daily_job.jsonl"

def lock_path() -> Path:
    return settings.storage_dir / "daily_job.lock"

def _jsonable(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else str(v)

def append_run(record: Dict[str, Any], path: Optional[Path] = None) -> Path:
    path = path or log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=_jsonable, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    return path

def read_runs(limit: int = 20, path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Latest `limit` runs, newest first."""
    path = path or log_path()
    if limit <= 0 or not path.exists():
        return []
    runs = []
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            runs.append(record)
            if len(runs) == limit:
                break
    return runs

def last_run(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    runs = read_runs(1, path)
    return runs[0] if runs else None

class LockHeld(RuntimeError):
    pass

class RunLock:
    """`with RunLock():` - raises LockHeld if another run holds a fresh lock."""

    def __init__(self, path: Optional[Path] = None, stale_after: timedelta = STALE_AFTER):
        self.path = path or lock_path()
        self.stale_after = stale_after
        self.took_over: Optional[Dict[str, Any]] = None  # contents of a stale lock that was replaced

    def _read(self) -> Dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _age(self) -> Optional[timedelta]:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return None
        return datetime.now(timezone.utc) - datetime.fromtimestamp(mtime, timezone.utc)

    def acquire(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                age = self._age()
                if age is not None and age < self.stale_after:
                    raise LockHeld(f"{self.path} held since {self._read().get('started_at', '?')}")
                self.took_over = self._read()
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "host": socket.gethostname(),
                           "started_at": datetime.now(timezone.utc).isoformat()}, f)
            return self
        raise LockHeld(f"{self.path} was re-created by another run")

    def release(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> "RunLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()

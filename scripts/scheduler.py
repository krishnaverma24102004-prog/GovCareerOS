"""
A minimal in-container scheduler for the docker compose "scheduler" service (on Windows, scripts/install_schedule.ps1
registers the daily job with Task Scheduler instead).

Each job is one of the existing scripts, started as its own process at its configured time:
  daily_job      scripts/daily_job.py            SCHEDULE_DAILY_JOB     (default "07:00"); with --skip digest while
                                                 send_digests is scheduled (see below)
  send_digests   scripts/send_digests.py         SCHEDULE_SEND_DIGESTS  (default "mon 08:00"); --send only when
                                                 SCHEDULE_DIGEST_SEND=true, otherwise digest files only
  triage         scripts/triage_notices.py --apply  SCHEDULE_TRIAGE     (default off: the daily job runs triage)
A time is "HH:MM" (every day) or "mon HH:MM" (weekly), at SCHEDULE_UTC_OFFSET (default +05:30, IST; India has no
DST, so a fixed offset is exact and the slim image needs no tz database). Jobs run one at a time, so they never
overlap; a job still running when another is due delays it. A run missed while the container was down is not
caught up (the daily job is idempotent and runs again the next day).

SIGTERM (docker stop; tini, compose "init: true", passes it on) or Ctrl+C: the running job gets SIGTERM and is waited
for (the daily job then logs the run and releases its lock), no further job starts, and the scheduler exits 0.

A digest reports what changed since the user's previous digest. So the digest is written by one job only: by
send_digests when it is scheduled (one digest a week, covering the week), otherwise by the daily job's digest step. If
both wrote one, the Monday digest would cover the hour since the 07:00 run and never show the week's new matches.

    python govcareeros/scripts/scheduler.py            # run forever
    python govcareeros/scripts/scheduler.py --list     # print the schedule and next run times, then exit
    python govcareeros/scripts/scheduler.py --run-now daily_job
"""
import argparse
import logging
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from govcareeros.backend.config.log_setup import configure_logging
from govcareeros.backend.config.settings import settings

SCRIPTS = Path(__file__).resolve().parent
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_SPEC = re.compile(r"^(?:(?P<day>[a-z]{3})\s+)?(?P<h>\d{1,2}):(?P<m>\d{2})$")
log = logging.getLogger("govcareeros.scheduler")

@dataclass(frozen=True)
class Job:
    name: str
    spec: str  # "HH:MM" or "mon HH:MM"
    argv: Sequence[str]

def parse_offset(text: str) -> timezone:
    m = re.fullmatch(r"([+-])(\d{2}):(\d{2})", text.strip())
    if not m:
        raise ValueError(f"SCHEDULE_UTC_OFFSET must look like +05:30, got {text!r}")
    delta = timedelta(hours=int(m[2]), minutes=int(m[3]))
    return timezone(delta if m[1] == "+" else -delta)

def next_run(spec: str, after: datetime) -> datetime:
    """The first time strictly after `after` (aware, in the zone the schedule is meant in) that matches spec."""
    m = _SPEC.match(spec.strip().lower())
    if not m or int(m["h"]) > 23 or int(m["m"]) > 59 or (m["day"] and m["day"] not in DAYS):
        raise ValueError(f'schedule must be "HH:MM" or "mon HH:MM", got {spec!r}')
    candidate = after.replace(hour=int(m["h"]), minute=int(m["m"]), second=0, microsecond=0)
    if m["day"]:
        candidate += timedelta(days=(DAYS.index(m["day"]) - candidate.weekday()) % 7)
        return candidate if candidate > after else candidate + timedelta(days=7)
    return candidate if candidate > after else candidate + timedelta(days=1)

def configured_jobs() -> List[Job]:
    py = sys.executable
    digests = [py, str(SCRIPTS / "send_digests.py")] + (["--send"] if settings.SCHEDULE_DIGEST_SEND else [])
    daily = [py, str(SCRIPTS / "daily_job.py")] + (["--skip", "digest"] if settings.SCHEDULE_SEND_DIGESTS.strip() else [])
    jobs = [Job("daily_job", settings.SCHEDULE_DAILY_JOB, daily),
            Job("send_digests", settings.SCHEDULE_SEND_DIGESTS, digests),
            Job("triage", settings.SCHEDULE_TRIAGE, [py, str(SCRIPTS / "triage_notices.py"), "--apply"])]
    jobs = [j for j in jobs if j.spec.strip()]
    for j in jobs:
        next_run(j.spec, datetime.now(timezone.utc))  # a bad spec fails at startup, not at 7 a.m.
    return jobs

# Set by SIGTERM / SIGINT: no job starts after it, and the running one is asked to stop. A plain flag, not a
# threading.Event: the handler runs between two bytecodes of the main thread, which may hold the Event's lock
_stop_requested = False
_running: Optional[subprocess.Popen] = None

def request_stop() -> None:
    global _stop_requested
    _stop_requested = True
    child = _running
    if child is not None and child.poll() is None:
        child.send_signal(signal.SIGTERM)

def _on_signal(signum, frame) -> None:
    log.info("signal %s: stopping", signum)
    request_stop()

def install_signal_handlers() -> None:
    # Python installs none for SIGTERM, and PID 1 ignores a signal it has no handler for
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

def run_job(job: Job) -> int:
    global _running
    log.info("job %s starting", job.name)
    started = time.monotonic()
    _running = proc = subprocess.Popen(list(job.argv))
    try:
        if _stop_requested:  # a stop that came in while the job was starting
            request_stop()
        while True:
            try:
                code = proc.wait(timeout=1)  # short waits: the signal handler runs between them on every platform
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        _running = None
    log.info("job %s finished with exit code %s after %.0fs", job.name, code, time.monotonic() - started)
    return code

def loop(jobs: List[Job], tz: timezone) -> None:
    now = datetime.now(tz)
    due = {j.name: next_run(j.spec, now) for j in jobs}
    for j in jobs:
        log.info("job %s scheduled %r, next run %s", j.name, j.spec, due[j.name].isoformat())
    while not _stop_requested:
        job = min(jobs, key=lambda j: due[j.name])
        wait = (due[job.name] - datetime.now(tz)).total_seconds()
        if wait > 0:
            # short sleeps: a stop is noticed within a second, a clock change cannot strand a job for hours
            time.sleep(min(wait, 1))
            continue
        run_job(job)
        due[job.name] = next_run(job.spec, datetime.now(tz))
        log.info("job %s next run %s", job.name, due[job.name].isoformat())

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--list", action="store_true", help="print the schedule and next run times, then exit")
    p.add_argument("--run-now", metavar="JOB", help="run one job once now and exit with its exit code")
    args = p.parse_args(argv)
    if not configure_logging(settings.LOG_FORMAT, settings.LOG_LEVEL):
        logging.basicConfig(level=settings.LOG_LEVEL.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    tz = parse_offset(settings.SCHEDULE_UTC_OFFSET)
    jobs = configured_jobs()
    install_signal_handlers()
    if args.run_now:
        job = next((j for j in jobs if j.name == args.run_now), None)
        if job is None:
            p.error(f"no enabled job {args.run_now!r}; enabled: {', '.join(j.name for j in jobs) or 'none'}")
        return run_job(job)
    if args.list:
        now = datetime.now(tz)
        for j in jobs:
            print(f"{j.name:<13} {j.spec:<10} next {next_run(j.spec, now).isoformat()}  {' '.join(j.argv[1:])}")
        return 0
    if not jobs:
        log.warning("no jobs enabled; nothing to do")
        return 0
    loop(jobs, tz)
    log.info("stopped")
    return 0

if __name__ == "__main__":
    sys.exit(main())

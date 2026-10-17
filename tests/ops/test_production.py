"""
Production packaging: the app under production settings (CORS origins, security headers, body limit, JSON logs with
secrets redacted), the compose scheduler's timing, and static checks of the Docker / compose files (Docker itself is
not needed). Every key, password and address here is a test-only value made up for the test.
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from govcareeros.backend.api.main import create_app
from govcareeros.backend.config import log_setup
from govcareeros.backend.config.redaction import REDACTED, redact
from govcareeros.backend.config.settings import Settings, settings
from govcareeros.backend.network.request_recorder import RequestRecorder
from govcareeros.scripts import scheduler

ROOT = Path(__file__).parents[2]
PROD_ORIGIN = "https://app.govcareeros.test"

def _client(app, base_url="http://test"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)

# ---------- settings ----------

def test_cors_origins_from_env(monkeypatch):
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:3000")
    monkeypatch.setenv("FRONTEND_ORIGINS", f" {PROD_ORIGIN}/, https://www.govcareeros.test ,,http://localhost:3000")
    s = Settings(_env_file=None)
    assert s.cors_origins == ["http://localhost:3000", PROD_ORIGIN, "https://www.govcareeros.test"]

    monkeypatch.setenv("FRONTEND_ORIGINS", "*")
    with pytest.raises(ValueError, match="explicit origins"):
        Settings(_env_file=None).cors_origins

def test_database_password_kept_out_of_the_url(monkeypatch):
    """compose passes POSTGRES_PASSWORD as DATABASE_PASSWORD next to a URL without one. Pasted into the URL raw,
    "ab@cd12" parsed as password "ab" at host "cd12@postgres", and "p%41ssword" as "pAssword"."""
    import configparser
    from sqlalchemy.engine import make_url
    from govcareeros.backend.config import redaction

    url = "postgresql+asyncpg://govcareeros@postgres:5432/govcareeros"
    for password in ("ab@cd12", "p%41ssword", "x:y/z?#[]@ test-only"):
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("DATABASE_PASSWORD", password)
        s = Settings(_env_file=None)
        parsed = make_url(s.DATABASE_URL)
        assert (parsed.username, parsed.password, parsed.host, parsed.port, parsed.database) ==             ("govcareeros", password, "postgres", 5432, "govcareeros")
        # alembic/env.py hands the URL to configparser with % doubled; it reads back as the same URL
        cp = configparser.ConfigParser()
        cp["alembic"] = {"sqlalchemy.url": s.DATABASE_URL.replace("%", "%%")}
        assert make_url(cp.get("alembic", "sqlalchemy.url")).password == password
        # and the log redaction knows the real password
        monkeypatch.setattr(settings, "DATABASE_URL", s.DATABASE_URL)
        assert password in redaction.configured_secrets()
    monkeypatch.delenv("DATABASE_PASSWORD")
    assert Settings(_env_file=None).DATABASE_URL == url  # no DATABASE_PASSWORD: the URL as given

# ---------- the app under production settings ----------

@pytest.mark.asyncio
async def test_extra_origin_allowed_others_refused(monkeypatch):
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", PROD_ORIGIN)
    async with _client(create_app()) as c:
        ok = await c.get("/health", headers={"Origin": PROD_ORIGIN})
        assert ok.headers["access-control-allow-origin"] == PROD_ORIGIN
        assert (await c.get("/health", headers={"Origin": settings.FRONTEND_ORIGIN})).headers[
            "access-control-allow-origin"] == settings.FRONTEND_ORIGIN
        refused = await c.get("/health", headers={"Origin": "https://evil.govcareeros.test"})
        assert "access-control-allow-origin" not in refused.headers

@pytest.mark.asyncio
async def test_security_headers_and_hsts_only_over_https(monkeypatch):
    monkeypatch.setattr(settings, "HSTS_SECONDS", 600)
    app = create_app()
    async with _client(app) as c:
        r = await c.get("/health")
        assert r.status_code == 200
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["content-security-policy"].startswith("default-src 'none'")
        assert "strict-transport-security" not in r.headers
        # the Swagger page is HTML that loads its own scripts: no JSON CSP on it, the other headers still apply
        docs = await c.get("/docs")
        assert docs.status_code == 200 and "content-security-policy" not in docs.headers
        assert docs.headers["x-frame-options"] == "DENY"
    async with _client(app, "https://test") as c:
        assert (await c.get("/health")).headers["strict-transport-security"] == "max-age=600; includeSubDomains"
        # CORS preflight answers carry them too (the middleware is outermost)
        pre = await c.options("/profile", headers={"Origin": settings.FRONTEND_ORIGIN, "Access-Control-Request-Method": "GET"})
        assert pre.status_code == 200 and pre.headers["x-content-type-options"] == "nosniff"
    monkeypatch.setattr(settings, "HSTS_SECONDS", 0)
    async with _client(app, "https://test") as c:
        assert "strict-transport-security" not in (await c.get("/health")).headers

@pytest.mark.asyncio
async def test_default_body_limit_applies_where_no_path_limit(api, monkeypatch):
    monkeypatch.setattr(settings, "MAX_REQUEST_BYTES", 1000)
    # refused before the route runs; the 413 still has the security headers
    big = await api.post("/auth/login", content=b'{"email": "' + b"a" * 2000 + b'"}', headers={"Content-Type": "application/json"})
    assert big.status_code == 413
    assert big.json()["detail"] == "request body is larger than 1,000 bytes"
    assert big.headers["x-content-type-options"] == "nosniff"
    # /verify keeps its own, larger limit: a 5,000-byte form reaches the route (which wants some text)
    form = await api.post("/verify", data={"text": " " * 5000})
    assert form.status_code == 422 and form.json()["detail"] == "paste the notice text, a link, or upload a PDF"

def test_app_imports_and_serves_with_production_env(tmp_path):
    """A fresh interpreter with the environment the images set: JSON logs on stdout, configured secrets redacted."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FRONTEND_", "LOG_", "NVIDIA_", "HSTS_"))}
    env.update({
        "PYTHONPATH": str(ROOT.parent), "PYTHONIOENCODING": "utf-8",
        "DATABASE_URL": f"sqlite+aiosqlite:///{(tmp_path / 'prod.db').as_posix()}", "TEST_DATABASE_URL": "",
        "STORAGE_PATH": str(tmp_path / "storage"), "LOG_FORMAT": "json", "LOG_LEVEL": "INFO",
        "FRONTEND_ORIGIN": PROD_ORIGIN, "FRONTEND_ORIGINS": "https://www.govcareeros.test",
        "NVIDIA_API_KEY": "nvapi-test-only-0123456789", "HSTS_SECONDS": "31536000",
    })
    code = r"""
import asyncio, json, logging, httpx
from govcareeros.backend.api.main import app
from govcareeros.backend.config.settings import settings
async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://api.govcareeros.test") as c:
        r = await c.get("/health", headers={"Origin": "https://www.govcareeros.test"})
    logging.getLogger("uvicorn.access").info("GET /x?token=abc123secret key=%s", settings.NVIDIA_API_KEY)
    print("RESULT " + json.dumps({"status": r.status_code, "origins": settings.cors_origins, "hsts": r.headers.get("strict-transport-security"),
                                  "acao": r.headers.get("access-control-allow-origin")}))
asyncio.run(main())
"""
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(ROOT.parent), capture_output=True, text=True,
                         encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    result = json.loads(next(l for l in lines if l.startswith("RESULT "))[7:])
    assert result == {"status": 200, "origins": [PROD_ORIGIN, "https://www.govcareeros.test"],
                      "hsts": "max-age=31536000; includeSubDomains", "acao": "https://www.govcareeros.test"}
    logged = [json.loads(l) for l in lines if l.startswith("{")]
    access = next(e for e in logged if e["logger"] == "uvicorn.access")
    assert access["level"] == "INFO" and access["msg"] == f"GET /x?token={REDACTED} key={REDACTED}"
    assert "nvapi-test-only" not in out.stdout and "abc123secret" not in out.stdout

# ---------- redaction and logging ----------

def test_redact_hides_secrets_keeps_the_rest():
    assert redact("connect postgresql+asyncpg://govcareeros:Pw0rd-test@postgres:5432/govcareeros") == \
        f"connect postgresql+asyncpg://govcareeros:{REDACTED}@postgres:5432/govcareeros"
    assert redact("Authorization: Bearer abc.def-ghi") == f"Authorization: {REDACTED}"
    assert redact("sent header Bearer abc.def-ghi") == f"sent header Bearer {REDACTED}"
    assert redact('{"access_token": "t0k3n", "email": "test@example.com"}') == \
        f'{{"access_token": "{REDACTED}", "email": "test@example.com"}}'
    assert redact("EMAIL_PASSWORD=hunter22 EMAIL_HOST=smtp.example.com") == f"EMAIL_PASSWORD={REDACTED} EMAIL_HOST=smtp.example.com"
    assert redact("key was test-only-secret-value here", ["test-only-secret-value"]) == f"key was {REDACTED} here"
    assert redact("GET /vacancies?page=2 200") == "GET /vacancies?page=2 200"
    # the network recorder's header redaction uses the same names
    assert RequestRecorder().sanitize_headers({"Cookie": "a=b", "Accept": "text/html"}) == {"Cookie": REDACTED, "Accept": "text/html"}

def test_redact_stays_linear_on_long_hostile_input():
    """Every access-log line passes through redact() on the API's event loop, and an anonymous client chooses the
    request path (h11 accepts a 16 KB request head). An unanchored key pattern took ~46 s on 16,000 word characters."""
    hostile = ["a" * 16000, "a." * 8000, "-" * 16000, "token" * 3200, "token." * 2700, "token" + " " * 16000,
               "x://" + "u" * 16000, "x://u:" + "p" * 16000, "ab://cd:" * 2000, "Bearer " * 2300, '"token"' * 2000]
    for text in hostile:
        started = time.perf_counter()
        redact(text)
        assert time.perf_counter() - started < 1.0, f"redact() is slow on {text[:12]!r} x {len(text)}"
    # a secret after a long path is still found
    assert redact("GET /" + "a" * 16000 + "?token=s3cr3t-test HTTP/1.1").endswith(f"a?token={REDACTED} HTTP/1.1")

def test_json_formatter_caps_long_messages_after_redacting():
    secret = "test-only-secret-0123456789"
    fmt = log_setup.JsonFormatter(secrets=[secret])
    # the configured secret straddles the cut: cutting first would leave its first 8 characters in the log
    text = "a" * (log_setup.MAX_MESSAGE_CHARS - 9) + " " + secret + " tail"
    record = logging.LogRecord("govcareeros.test", logging.INFO, __file__, 1, "%s", (text,), None)
    msg = json.loads(fmt.format(record))["msg"]
    assert msg.startswith("aaa") and msg.endswith("more characters cut]")
    assert len(msg) < log_setup.MAX_MESSAGE_CHARS + 50
    assert "test-onl" not in msg and "[REDACTE" in msg

def test_json_formatter_redacts_message_and_traceback():
    fmt = log_setup.JsonFormatter(secrets=["test-only-key-987654"])
    try:
        raise RuntimeError("upstream refused key test-only-key-987654")
    except RuntimeError:
        record = logging.LogRecord("govcareeros.llm", logging.ERROR, __file__, 1, "call failed password=%s", ("p4ss",),
                                   sys.exc_info())
    entry = json.loads(fmt.format(record))
    assert entry["level"] == "ERROR" and entry["logger"] == "govcareeros.llm"
    assert entry["msg"] == f"call failed password={REDACTED}"
    assert "RuntimeError: upstream refused key " + REDACTED in entry["exc"]
    assert "test-only-key-987654" not in json.dumps(entry)
    assert log_setup.configure_logging("text") is False  # text: logging left as it is

# ---------- scheduler ----------

IST = timezone(timedelta(hours=5, minutes=30))

def test_scheduler_next_run():
    assert scheduler.parse_offset("+05:30") == IST
    assert scheduler.parse_offset("-03:00") == timezone(-timedelta(hours=3))
    with pytest.raises(ValueError):
        scheduler.parse_offset("IST")
    wed_6am = datetime(2026, 10, 7, 6, 0, tzinfo=IST)  # a Wednesday
    assert scheduler.next_run("07:00", wed_6am) == datetime(2026, 10, 7, 7, 0, tzinfo=IST)
    assert scheduler.next_run("07:00", wed_6am.replace(hour=7)) == datetime(2026, 10, 8, 7, 0, tzinfo=IST)
    assert scheduler.next_run("mon 08:00", wed_6am) == datetime(2026, 10, 12, 8, 0, tzinfo=IST)
    assert scheduler.next_run("wed 05:00", wed_6am) == datetime(2026, 10, 14, 5, 0, tzinfo=IST)
    assert scheduler.next_run("WED 06:30", wed_6am) == datetime(2026, 10, 7, 6, 30, tzinfo=IST)
    for bad in ("7am", "25:00", "07:60", "xyz 07:00", "daily"):
        with pytest.raises(ValueError):
            scheduler.next_run(bad, wed_6am)

def test_scheduler_jobs_from_settings(monkeypatch):
    jobs = {j.name: j for j in scheduler.configured_jobs()}
    assert set(jobs) == {"daily_job", "send_digests"}  # triage is off by default: the daily job runs it
    assert jobs["daily_job"].argv[1].endswith("daily_job.py")
    # the weekly job writes the digest, so the daily one does not (tests/ops/test_daily_job.py: the Monday sequence)
    assert jobs["daily_job"].argv[2:] == ["--skip", "digest"]
    assert "--send" not in jobs["send_digests"].argv  # e-mail only when explicitly turned on
    monkeypatch.setattr(settings, "SCHEDULE_SEND_DIGESTS", "")
    jobs = {j.name: j for j in scheduler.configured_jobs()}
    assert set(jobs) == {"daily_job"} and jobs["daily_job"].argv[2:] == []  # no weekly job: the daily job writes them
    monkeypatch.setattr(settings, "SCHEDULE_SEND_DIGESTS", "mon 08:00")

    monkeypatch.setattr(settings, "SCHEDULE_DIGEST_SEND", True)
    monkeypatch.setattr(settings, "SCHEDULE_TRIAGE", "19:00")
    monkeypatch.setattr(settings, "SCHEDULE_DAILY_JOB", "")
    jobs = {j.name: j for j in scheduler.configured_jobs()}
    assert set(jobs) == {"send_digests", "triage"}
    assert jobs["send_digests"].argv[-1] == "--send"
    assert jobs["triage"].argv[-2].endswith("triage_notices.py") and jobs["triage"].argv[-1] == "--apply"

    monkeypatch.setattr(settings, "SCHEDULE_TRIAGE", "7pm")
    with pytest.raises(ValueError):
        scheduler.configured_jobs()

_SCHEDULER_STOP = r"""
import signal, sys, threading, time
from datetime import datetime, timedelta
from govcareeros.scripts import scheduler

mode = sys.argv[1]
tz = scheduler.parse_offset("+05:30")
scheduler.install_signal_handlers()
threading.Timer(1.0, signal.raise_signal, (signal.SIGTERM,)).start()  # what docker stop delivers
started = time.monotonic()
sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
if mode == "running_job":
    code = scheduler.run_job(scheduler.Job("sleeper", "07:00", sleeper))
    print(f"JOB {code} {time.monotonic() - started:.1f}")
    # stopping: the loop starts no further job, even one that is due
    due_now = (datetime.now(tz) - timedelta(minutes=1)).strftime("%H:%M")
    scheduler.loop([scheduler.Job("sleeper", due_now, sleeper)], tz)
else:  # waiting for a job due in an hour
    scheduler.loop([scheduler.Job("sleeper", (datetime.now(tz) + timedelta(hours=1)).strftime("%H:%M"), sleeper)], tz)
print(f"STOPPED {time.monotonic() - started:.1f}")
"""

@pytest.mark.parametrize("mode", ["running_job", "idle"])
def test_scheduler_stops_on_sigterm_and_stops_its_job(tmp_path, mode):
    """docker stop sends SIGTERM to the scheduler. Python installs no SIGTERM handler, and as PID 1 an unhandled
    SIGTERM is ignored, so every stop waited out the grace period and then SIGKILLed a running job."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LOG_", "SCHEDULE_"))}
    env.update({"PYTHONPATH": str(ROOT.parent), "PYTHONIOENCODING": "utf-8", "TEST_DATABASE_URL": "", "STORAGE_PATH": str(tmp_path)})
    out = subprocess.run([sys.executable, "-c", _SCHEDULER_STOP, mode], env=env, cwd=str(tmp_path), capture_output=True,
                         text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    lines = dict(l.split(" ", 1) for l in out.stdout.splitlines() if l.startswith(("JOB ", "STOPPED ")))
    assert float(lines["STOPPED"]) < 15, out.stdout  # the 60 s job and the hour-long wait were both cut short
    if mode == "running_job":
        code, _ = lines["JOB"].split()
        assert int(code) != 0  # the job was stopped, not left to finish

# ---------- Docker / compose files (static; Docker is not needed) ----------

def test_compose_file_structure():
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert set(services) == {"postgres", "migrate", "api", "scheduler", "web"}
    assert services["postgres"]["image"] == "postgres:17"
    assert "pgdata:/var/lib/postgresql/data" in services["postgres"]["volumes"]
    assert services["migrate"]["command"][-2:] == ["upgrade", "head"]
    for name in ("api", "scheduler"):
        assert services[name]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert services["scheduler"]["command"][-1] == "govcareeros/scripts/scheduler.py"
    # tini as PID 1 passes docker stop's SIGTERM to scheduler.py and reaps orphaned browser processes
    assert services["scheduler"]["init"] is True and services["scheduler"]["stop_grace_period"]
    # the password reaches the app on its own, never pasted into the URL (it may contain @ : / %)
    for name in ("migrate", "api", "scheduler"):
        env = services[name]["environment"]
        assert "POSTGRES_PASSWORD" not in env["DATABASE_URL"] and env["DATABASE_URL"].startswith("postgresql+asyncpg://govcareeros@")
        assert env["DATABASE_PASSWORD"].startswith("${POSTGRES_PASSWORD:?")
    # published only on loopback; the reverse proxy is the public entry
    for name in ("api", "web"):
        assert all(p.startswith("127.0.0.1:") for p in services[name]["ports"])

def test_images_never_contain_env_files():
    for ignore in (ROOT / ".dockerignore", ROOT / "frontend" / ".dockerignore"):
        lines = ignore.read_text(encoding="utf-8").splitlines()
        assert ".env" in lines and ".env.*" in lines
    api = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER app" in api and "PYTHONPATH=/app" in api and "/app/govcareeros" in api
    assert "/health" in api and "ARG INSTALL_PLAYWRIGHT=0" in api
    web = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    assert "USER node" in web and ".next/standalone" in web and "NEXT_OUTPUT=standalone" in web
    for dockerfile in (api, web):
        assert not [l for l in dockerfile.splitlines() if l.startswith(("COPY", "ADD")) and ".env" in l]

# ---------- dashboard headers (frontend/next.config.ts, evaluated by node) ----------

def _next_config_headers(env, config=ROOT / "frontend" / "next.config.ts"):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = ("import(require('url').pathToFileURL(process.argv[1]).href).then(m => m.default.headers())"
              ".then(h => console.log('HEADERS ' + JSON.stringify(h)))")
    out = subprocess.run([node, "-e", script, str(config)], env={**os.environ, **env}, capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    if "ERR_UNKNOWN_FILE_EXTENSION" in out.stderr:
        pytest.skip("this node cannot load TypeScript directly (needs 22.18 or later)")
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(next(l for l in out.stdout.splitlines() if l.startswith("HEADERS "))[8:])

def test_dashboard_sends_hsts_over_https_and_a_csp_in_production(tmp_path):
    """Users log in on the dashboard, a sibling host of the API: the API's HSTS does not cover it, and the bearer
    token lives in sessionStorage. Caddy adds neither header by default."""
    rules = _next_config_headers({"NODE_ENV": "production", "NEXT_PUBLIC_API_URL": "https://api.govcareeros.test/"})
    every = [r for r in rules if "has" not in r]
    assert [r["source"] for r in every] == ["/:path*"]
    headers = {h["key"]: h["value"] for h in every[0]["headers"]}
    assert headers["X-Frame-Options"] == "DENY" and headers["X-Content-Type-Options"] == "nosniff"
    csp = dict(d.split(" ", 1) for d in headers["Content-Security-Policy"].split("; "))
    assert csp["default-src"] == "'self'" and csp["connect-src"] == "'self' https://api.govcareeros.test"
    assert csp["object-src"] == csp["frame-ancestors"] == "'none'" and csp["base-uri"] == csp["form-action"] == "'self'"
    assert "'unsafe-eval'" not in headers["Content-Security-Policy"]
    # HSTS on requests that came over https (X-Forwarded-Proto from the TLS proxy), never on plain http
    [https] = [r for r in rules if "has" in r]
    assert https["source"] == "/:path*" and https["has"] == [{"type": "header", "key": "x-forwarded-proto", "value": "https"}]
    assert https["headers"] == [{"key": "Strict-Transport-Security", "value": "max-age=31536000; includeSubDomains"}]
    # next dev: no CSP (fast refresh needs eval and a websocket) and no HSTS
    dev = _next_config_headers({"NODE_ENV": "development"})
    assert len(dev) == 1 and "Content-Security-Policy" not in json.dumps(dev) and "Strict-Transport" not in json.dumps(dev)

# ---------- the API image's dependency set ----------

# What the images import at startup: uvicorn's app, the scheduler and the jobs it starts (with the services they load)
_IMAGE_IMPORTS = ["govcareeros.backend.api.main", "govcareeros.backend.models", "govcareeros.scripts.scheduler",
                  "govcareeros.scripts.daily_job", "govcareeros.scripts.send_digests", "govcareeros.scripts.triage_notices",
                  "govcareeros.backend.services.source_discovery", "govcareeros.backend.services.document_ingestion",
                  "govcareeros.backend.services.triage", "govcareeros.backend.services.extraction_drafts",
                  "govcareeros.backend.services.radar", "govcareeros.backend.services.digest", "govcareeros.backend.llm.provider"]

def _runtime_requirements():
    """Requirement lines of requirements.txt above the "# dev" marker: exactly what the Dockerfile installs."""
    from packaging.requirements import Requirement
    lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    runtime = []
    for line in lines:
        if line.startswith("# dev"):
            break
        if line.strip() and not line.lstrip().startswith("#"):
            runtime.append(Requirement(line.strip()))
    return runtime

def _install_closure(requirements):
    """Normalised names of every distribution pip installs for these requirements (from the installed metadata)."""
    from importlib import metadata
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    seen, todo = set(), [(r, frozenset(r.extras)) for r in requirements]
    while todo:
        req, extras = todo.pop()
        name = canonicalize_name(req.name)
        if name in seen:
            continue
        seen.add(name)
        try:
            dist = metadata.distribution(req.name)
        except metadata.PackageNotFoundError:
            continue  # not installed here, so nothing of it can be imported here either
        for line in dist.requires or []:
            sub = Requirement(line)
            if sub.marker is None or any(sub.marker.evaluate({"extra": e}) for e in (extras or {""})):
                todo.append((sub, frozenset(sub.extras)))
    return seen

def test_runtime_requirements_cover_what_the_images_import(tmp_path):
    """The API image installs only the runtime section of requirements.txt. Import the app and the scheduled jobs
    with every module outside that section's install closure hidden: a package that is here only because it was
    installed by hand (python-multipart for the /verify form) would otherwise crash the api container at startup."""
    pytest.importorskip("packaging")
    from importlib import metadata
    from packaging.utils import canonicalize_name

    closure = _install_closure(_runtime_requirements())
    hidden = sorted(module for module, dists in metadata.packages_distributions().items()
                    if module.isidentifier() and not any(canonicalize_name(d) in closure for d in dists))
    code = r"""
import importlib, json, sys
from importlib.abc import MetaPathFinder
hidden = set(json.loads(sys.argv[1]))
class Hide(MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in hidden:
            raise ModuleNotFoundError(f"No module named {name!r} (not in the image's runtime requirements)", name=name)
        return None
sys.meta_path.insert(0, Hide())
for module in json.loads(sys.argv[2]):
    importlib.import_module(module)
print("IMPORTED")
"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FRONTEND_", "LOG_"))}
    env.update({"PYTHONPATH": str(ROOT.parent), "PYTHONIOENCODING": "utf-8", "TEST_DATABASE_URL": "",
                "DATABASE_URL": f"sqlite+aiosqlite:///{(tmp_path / 'img.db').as_posix()}", "STORAGE_PATH": str(tmp_path / "storage")})
    out = subprocess.run([sys.executable, "-c", code, json.dumps(hidden), json.dumps(_IMAGE_IMPORTS)], env=env,
                         cwd=str(tmp_path), capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert out.returncode == 0 and "IMPORTED" in out.stdout, out.stderr[-3000:]

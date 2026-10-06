"""
Production packaging: the app under production settings (CORS origins, security headers, body limit, JSON logs with
secrets redacted), the compose scheduler's timing, and static checks of the Docker / compose files (Docker itself is
not needed). Every key, password and address here is a test-only value made up for the test.
"""
import json
import logging
import os
import subprocess
import sys
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
    assert "--send" not in jobs["send_digests"].argv  # e-mail only when explicitly turned on

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

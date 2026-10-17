from pathlib import Path
from typing import List, Optional
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    # Real environment variables win over values in .env
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/govcareeros"
    # Optional: the database password on its own, any characters (docker-compose.yml passes POSTGRES_PASSWORD here).
    # It replaces the password in DATABASE_URL, escaped, so "@", ":", "/" or "%41" in it cannot change the URL
    DATABASE_PASSWORD: str = ""
    TEST_DATABASE_URL: Optional[str] = None
    DB_ECHO: bool = False
    REDIS_URL: str = "redis://localhost:6379/0"

    # Local-first default: a model served by the local Ollama install
    LLM_PROVIDER: str = "ollama"
    LLM_MODEL: str = "huihui_ai/qwen3-abliterated:8b-v2-q4_K_M"
    OLLAMA_URL: str = "http://127.0.0.1:11434"
    LLM_TIMEOUT_SECONDS: float = 1200
    LLM_NUM_CTX: int = 8192
    NVIDIA_API_KEY: str = ""
    NVIDIA_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    # Tried in order; a model the API reports gone (404/410) is skipped for the rest of the run.
    # nvidia/nemotron-3-super-120b-a12b was retired 03-10-2026 (HTTP 410). Ultra handles Hindi
    # notices but is slow; 3.5-lightning is fast.
    NVIDIA_MODELS: str = "nvidia/nemotron-3-ultra-550b-a55b,nvidia/nemotron-3.5-lightning-30b-a3b"
    NVIDIA_REQUESTS_PER_MINUTE: float = 40  # free tier limit; request starts are spaced to stay under it
    GOOGLE_API_KEY: str = ""

    EMAIL_HOST: str = "smtp.gmail.com"
    EMAIL_PORT: int = 587
    EMAIL_USERNAME: str = ""
    EMAIL_PASSWORD: str = ""

    # The browser origin allowed to call the API (the local Next.js dashboard)
    FRONTEND_ORIGIN: str = "http://localhost:3000"
    # Production: further allowed origins, comma-separated (e.g. "https://govcareeros.example,https://www.govcareeros.example").
    # Explicit origins only: "*" is refused at startup
    FRONTEND_ORIGINS: str = ""

    # Largest request body accepted by any route without its own limit (the notice check allows a 10 MB PDF)
    MAX_REQUEST_BYTES: int = 1024 * 1024
    # Strict-Transport-Security max-age, sent only on https requests; 0 = never. Behind a TLS-terminating proxy run
    # uvicorn with --proxy-headers so the request scheme is the client's (docs/deploy/README.md)
    HSTS_SECONDS: int = 31_536_000
    # "text" (default, local) or "json": one JSON object per line on stdout, secrets redacted (config/log_setup.py)
    LOG_FORMAT: str = "text"
    LOG_LEVEL: str = "INFO"

    # docker compose scheduler (scripts/scheduler.py): "HH:MM" daily or "mon HH:MM" weekly, at SCHEDULE_UTC_OFFSET;
    # empty = job off. Triage already runs as a step of the daily job, so its own entry is off by default
    SCHEDULE_UTC_OFFSET: str = "+05:30"
    SCHEDULE_DAILY_JOB: str = "07:00"
    SCHEDULE_SEND_DIGESTS: str = "mon 08:00"
    SCHEDULE_TRIAGE: str = ""
    # The scheduled digest emails users only when true (and EMAIL_USERNAME / EMAIL_PASSWORD are set), and then only users
    # with an active ALERTS consent (services/digest.may_email); else files only
    SCHEDULE_DIGEST_SEND: bool = False

    STORAGE_PATH: str = str(PROJECT_ROOT / "storage")
    PLAYWRIGHT_HEADLESS: bool = True
    # Folder containing eng.traineddata; defaults to the standard Windows Tesseract install
    TESSDATA_DIR: Optional[str] = None

    # Days an in-service applicant's office may need to forward an application; 0 = off. Shown only as a labelled
    # estimate beside the official last date, never as the date (backend/services/deadline_helper.py)
    IN_SERVICE_FORWARDING_BUFFER_DAYS: int = 0

    @model_validator(mode="after")
    def _database_password(self) -> "Settings":
        if self.DATABASE_PASSWORD:
            from sqlalchemy.engine import make_url
            self.DATABASE_URL = make_url(self.DATABASE_URL).set(password=self.DATABASE_PASSWORD).render_as_string(hide_password=False)
        return self

    @property
    def cors_origins(self) -> List[str]:
        origins = [o.strip().rstrip("/") for o in [self.FRONTEND_ORIGIN, *self.FRONTEND_ORIGINS.split(",")] if o.strip()]
        if "*" in origins:
            raise ValueError('FRONTEND_ORIGIN(S) must list explicit origins, not "*"')
        return list(dict.fromkeys(origins))

    @property
    def storage_dir(self) -> Path:
        path = Path(self.STORAGE_PATH)
        return path if path.is_absolute() else PROJECT_ROOT / path

settings = Settings()

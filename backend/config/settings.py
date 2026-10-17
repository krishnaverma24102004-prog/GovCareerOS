from pathlib import Path
from typing import Optional
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    # Real environment variables win over values in .env
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/govcareeros"
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

    # The only browser origin allowed to call the API (the local Next.js dashboard)
    FRONTEND_ORIGIN: str = "http://localhost:3000"

    STORAGE_PATH: str = str(PROJECT_ROOT / "storage")
    PLAYWRIGHT_HEADLESS: bool = True
    # Folder containing eng.traineddata; defaults to the standard Windows Tesseract install
    TESSDATA_DIR: Optional[str] = None

    # Days an in-service applicant's office may need to forward an application; 0 = off. Shown only as a labelled
    # estimate beside the official last date, never as the date (backend/services/deadline_helper.py)
    IN_SERVICE_FORWARDING_BUFFER_DAYS: int = 0

    # Operator console billing is TEST MODE only (backend/services/billing.py): the default price of one per-application
    # report, in paise, when no billing_prices row applies. No real gateway exists; nothing is ever collected.
    OPERATOR_REPORT_PRICE_PAISE: int = 2000
    # Hours a client account claim token stays usable
    CLAIM_TOKEN_TTL_HOURS: int = 72
    # DPDP Act 2023. Key for field-level encryption of category/caste and disability columns: 32 random bytes,
    # urlsafe-base64 (python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())").
    # Set it in the environment / .env only; it is never logged or returned. Empty = those fields cannot be stored.
    FIELD_ENCRYPTION_KEY: SecretStr = SecretStr("")
    # Shown in the consent notice and privacy page (name, email, postal address of the grievance officer)
    PRIVACY_GRIEVANCE_CONTACT: str = ""
    # Days between an erasure request and the hard erase (the person may cancel by contacting the grievance officer)
    ERASURE_GRACE_DAYS: int = 30

    @property
    def storage_dir(self) -> Path:
        path = Path(self.STORAGE_PATH)
        return path if path.is_absolute() else PROJECT_ROOT / path

settings = Settings()

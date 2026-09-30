from pathlib import Path
from typing import Optional
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
    GOOGLE_API_KEY: str = ""

    EMAIL_HOST: str = "smtp.gmail.com"
    EMAIL_PORT: int = 587
    EMAIL_USERNAME: str = ""
    EMAIL_PASSWORD: str = ""

    STORAGE_PATH: str = str(PROJECT_ROOT / "storage")
    PLAYWRIGHT_HEADLESS: bool = True
    # Folder containing eng.traineddata; defaults to the standard Windows Tesseract install
    TESSDATA_DIR: Optional[str] = None

    @property
    def storage_dir(self) -> Path:
        path = Path(self.STORAGE_PATH)
        return path if path.is_absolute() else PROJECT_ROOT / path

settings = Settings()

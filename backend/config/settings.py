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

    LLM_PROVIDER: str = "gemini"
    LLM_MODEL: str = "gemini-1.5-pro"
    GOOGLE_API_KEY: str = ""

    EMAIL_HOST: str = "smtp.gmail.com"
    EMAIL_PORT: int = 587
    EMAIL_USERNAME: str = ""
    EMAIL_PASSWORD: str = ""

    STORAGE_PATH: str = str(PROJECT_ROOT / "storage")
    PLAYWRIGHT_HEADLESS: bool = True

    @property
    def storage_dir(self) -> Path:
        path = Path(self.STORAGE_PATH)
        return path if path.is_absolute() else PROJECT_ROOT / path

settings = Settings()

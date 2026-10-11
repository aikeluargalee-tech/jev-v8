from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "Jev v8 Decision-Support Terminal"
    APP_VERSION: str = "8.0.0"
    HOST: str = "127.0.0.1"  # Local-only bound
    PORT: int = 8000
    DEBUG: bool = False

    # Cost guardrail & calibration thresholds
    MONTHLY_BUDGET_USD: float = 20.0
    SHADOW_MODE_MIN_VERDICTS: int = 60
    CALIBRATION_MIN_OUTCOMES: int = 100

    # Market defaults
    DEFAULT_VENUE: str = "kraken"
    DEFAULT_PAIR: str = "BTC/USD"
    REQUEST_TIMEOUT: float = 15.0

    # Phase 2 flags: Real Jev disabled by default for zero cost
    ENABLE_JEV: bool = False
    TYPESAFE_API_KEY: Optional[str] = None
    TWITTER_API_KEY: Optional[str] = None

    # Caching
    CACHE_TTL_SECONDS: int = 600

    # Security
    ALLOWED_ORIGINS: str = "http://127.0.0.1:8000,http://localhost:8000"
    ADMIN_TOKEN: Optional[str] = None


settings = Settings()

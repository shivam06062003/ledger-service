from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables (and `.env` locally).

    Each field maps to an upper-cased env var, e.g. `database_url` <- `DATABASE_URL`.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "ledger-service"
    environment: Literal["local", "test", "production"] = "local"

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True

    database_url: str = "postgresql+asyncpg://ledger:ledger@localhost:5432/ledger"
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # How long a stored Idempotency-Key response is kept for replay.
    idempotency_key_retention_hours: int = 24


@lru_cache
def get_settings() -> Settings:
    return Settings()

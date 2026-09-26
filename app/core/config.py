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

    # Webhook delivery (worker process)
    webhook_timeout_seconds: float = 10.0
    webhook_max_attempts: int = 10
    webhook_backoff_base_seconds: float = 10.0
    webhook_backoff_cap_seconds: float = 3600.0
    # How long a claimed delivery is hidden from other workers. Must exceed the
    # HTTP timeout, or a slow delivery could be picked up twice.
    webhook_lease_seconds: int = 60
    worker_batch_size: int = 50
    worker_poll_interval_seconds: float = 1.0
    worker_heartbeat_path: str = "/tmp/worker-heartbeat"
    worker_metrics_port: int = 9100

    # Rate limiting (token bucket per API key, stored in Redis)
    redis_url: str = "redis://localhost:6380/0"
    rate_limit_enabled: bool = True
    rate_limit_per_second: float = 50.0
    rate_limit_burst: int = 100

    # Scheduled jobs (run by the worker; once per interval across all replicas)
    reconciliation_interval_seconds: int = 300
    retention_job_interval_seconds: int = 3600
    webhook_history_retention_days: int = 30

    # Tracing (OpenTelemetry, exported over OTLP/HTTP, e.g. to Jaeger)
    otel_enabled: bool = False
    otel_service_name: str = "ledger-api"
    otel_exporter_otlp_endpoint: str = "http://localhost:4318/v1/traces"


@lru_cache
def get_settings() -> Settings:
    return Settings()

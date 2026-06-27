"""Application settings, sourced from environment / .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Upstream Hospital Directory API.
    upstream_base_url: str = "https://hospital-directory.onrender.com"

    # Concurrency: max simultaneous create calls against the (free-tier) upstream.
    # Bounded on purpose — unbounded gather melts a free Render box (see README).
    max_concurrency: int = 10

    # Per-call HTTP timeout, seconds. Generous to absorb Render cold starts.
    request_timeout_seconds: float = 30.0

    # Retry policy for transient upstream failures (timeouts, 502/503).
    max_retries: int = 3
    backoff_base_seconds: float = 0.5

    # Hard cap on rows per CSV (spec: <= 20).
    max_rows: int = 20

    # Log verbosity (DEBUG | INFO | WARNING | ERROR).
    log_level: str = "INFO"

    # Activation policy on partial failure.
    #   False (strict, default) -> activate only when failed == 0 (matches spec literal).
    #   True  (lenient)         -> activate the batch even if some rows failed.
    activate_on_partial_failure: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (prefix CRYPTO_) or a local .env file."""

    model_config = SettingsConfigDict(env_prefix="CRYPTO_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/kraken_spot_analysis"
    kraken_base_url: str = "https://api.kraken.com/0/public"
    # Kraken's public endpoints allow roughly one call per second.
    request_interval_s: float = 1.0
    http_timeout_s: float = 30.0
    max_retries: int = 5
    quote_asset: str = "ZUSD"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration. Request code reads these values and does not embed endpoints."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "ollama"
    ollama_base_url: str | None = None
    ollama_model: str | None = None
    ollama_timeout_ms: int = 180000
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_timeout_ms: int = 180000
    explanation_packet_char_budget: int = 32000
    database_url: str = "sqlite:///./pr_explain.db"
    github_app_id: str | None = None
    github_app_private_key: str | None = None
    github_app_private_key_file: str | None = None
    github_webhook_secret: str | None = None
    app_base_url: str = ""
    github_api_url: str = "https://api.github.com"
    cors_origins: str = "http://localhost:5173"
    fanout_cap: int = 50
    max_changed_symbols: int = 80
    run_migrations: bool = True
    worker_id: str = "worker"
    worker_poll_seconds: float = 1.0


@lru_cache
def get_settings() -> Settings:
    return Settings()

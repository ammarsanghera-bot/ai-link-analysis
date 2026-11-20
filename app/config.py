"""Application configuration loaded from environment variables and ``.env``."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ProviderName = Literal["openai", "gemini"]


class Settings(BaseSettings):
    """Typed application settings.

    Values come from (highest priority first) explicit constructor arguments,
    environment variables, then the local ``.env`` file. Empty environment
    values are ignored so that ``OPENAI_MODEL=`` falls back to the default.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    # --- Application --------------------------------------------------------
    app_name: str = "AI-Powered Website Link Analysis API"
    app_version: str = "1.0.0"
    environment: str = "development"
    host: str = "0.0.0.0"
    port: int = Field(default=4000, ge=1, le=65535)
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    # --- LLM providers ------------------------------------------------------
    llm_provider: ProviderName = "openai"
    llm_fallback_provider: ProviderName | None = None

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-6.1-terra"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_reasoning_effort: str | None = None

    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-4-hydrogen"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    llm_timeout_seconds: float = Field(default=90.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_retry_backoff_seconds: float = Field(default=1.0, ge=0)
    llm_max_output_tokens: int = Field(default=8000, ge=512)

    # --- Website extraction -------------------------------------------------
    user_agent: str = "AILinkAnalysisBot/1.0 (website analysis API; fetches pages on user request)"
    fetch_timeout_seconds: float = Field(default=10.0, gt=0)
    fetch_total_timeout_seconds: float = Field(default=30.0, gt=0)
    max_pages: int = Field(default=5, ge=1, le=10)
    max_redirects: int = Field(default=5, ge=0, le=10)
    max_response_bytes: int = Field(default=2_000_000, ge=10_000)
    max_content_chars: int = Field(default=24_000, ge=1_000)
    allowed_ports: Annotated[list[int], NoDecode] = [80, 443]

    # --- Result cache -------------------------------------------------------
    cache_ttl_seconds: int = Field(default=3600, ge=0)
    cache_max_entries: int = Field(default=128, ge=1)

    @field_validator("llm_provider", "llm_fallback_provider", mode="before")
    @classmethod
    def _normalise_provider(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("allowed_ports", mode="before")
    @classmethod
    def _split_ports(cls, value: object) -> object:
        """Accept a comma-separated string such as ``80,443``."""
        if isinstance(value, str):
            return [int(port) for port in value.split(",") if port.strip()]
        return value

    def api_key_for(self, provider: ProviderName) -> str | None:
        """Return the API key for ``provider``, or ``None`` when it is not set."""
        secret: SecretStr | None = getattr(self, f"{provider}_api_key")
        value = secret.get_secret_value().strip() if secret else ""
        return value or None

    def model_for(self, provider: ProviderName) -> str:
        """Return the configured model name for ``provider``."""
        return getattr(self, f"{provider}_model")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()

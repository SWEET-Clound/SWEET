from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MEMORY_",
        env_file=".env",
        extra="forbid",
    )

    database_url: SecretStr
    worker_database_url: SecretStr | None = None
    environment: Literal["development", "production"] = "development"
    jwt_issuer: str
    jwt_audience: str
    jwks_url: str | None = None
    dev_jwt_secret: SecretStr | None = None
    extraction_mode: Literal["heuristic", "shadow", "model", "hybrid"] = "heuristic"
    model_base_url: str | None = None
    model_api_key: SecretStr | None = None
    extraction_model: str | None = None
    model_timeout_seconds: float = Field(default=30, ge=1, le=300)
    model_max_attempts: int = Field(default=2, ge=1, le=3)
    embedding_model: str | None = None
    rerank_model: str | None = None
    raw_event_retention_days: int = Field(default=30, ge=1)
    working_memory_ttl_hours: int = Field(default=72, ge=1)
    max_context_tokens: int = Field(default=2048, ge=256, le=4096)
    event_write_limit_per_minute: int = Field(default=120, ge=1, le=100_000)
    command_write_limit_per_minute: int = Field(default=30, ge=1, le=100_000)
    worker_concurrency: int = Field(default=4, ge=1, le=64)
    worker_poll_seconds: float = Field(default=1.0, ge=0.1, le=60)

    @model_validator(mode="after")
    def validate_security_mode(self) -> "Settings":
        if self.environment == "production":
            if self.dev_jwt_secret is not None:
                raise ValueError("development JWT secret is forbidden in production")
            if not self.jwks_url:
                raise ValueError("JWKS URL is required in production")
        elif self.dev_jwt_secret is None and not self.jwks_url:
            raise ValueError("development requires a JWT secret or JWKS URL")
        if self.dev_jwt_secret is not None:
            secret = self.dev_jwt_secret.get_secret_value()
            if len(secret) < 32:
                raise ValueError("development JWT secret must be at least 32 characters")
        if self.extraction_mode in {"shadow", "model", "hybrid"} and (
            not self.model_base_url or not self.extraction_model
        ):
            raise ValueError("model extraction requires model_base_url and extraction_model")
        return self


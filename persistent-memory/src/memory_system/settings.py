from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from memory_system.event_cipher import EventCipher


def require_verified_database_tls(database_url: str, name: str) -> None:
    try:
        query = parse_qs(urlsplit(database_url).query, keep_blank_values=True)
    except ValueError:
        raise ValueError(f"{name} is invalid") from None
    if query.get("sslmode") != ["verify-full"]:
        raise ValueError(f"{name} must specify sslmode=verify-full in production")


def _read_event_key(value: SecretStr | None, path: Path | None, name: str) -> str | None:
    if path is None:
        return value.get_secret_value() if value is not None else None
    if value is not None:
        raise ValueError(f"configure only one of {name} and {name}_FILE")
    try:
        with path.open(encoding="ascii") as secret_file:
            encoded_key = secret_file.read(129)
    except (OSError, UnicodeError):
        raise ValueError(f"cannot read {name}_FILE") from None
    if len(encoded_key) > 128:
        raise ValueError(f"{name}_FILE is too large")
    return encoded_key.strip("\r\n")


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
    event_encryption_key: SecretStr | None = None
    event_encryption_key_file: Path | None = None
    event_encryption_fallback_key: SecretStr | None = None
    event_encryption_fallback_key_file: Path | None = None
    event_encryption_rotate: bool = False
    extraction_model: str | None = None
    model_timeout_seconds: float = Field(default=30, ge=1, le=300)
    model_max_attempts: int = Field(default=2, ge=1, le=3)
    embedding_model: str | None = None
    embedding_dimensions: int | None = Field(default=None, ge=1, le=4096)
    rerank_model: str | None = None
    raw_event_retention_days: int = Field(default=30, ge=1)
    working_memory_ttl_hours: int = Field(default=72, ge=1)
    max_context_tokens: int = Field(default=2048, ge=256, le=4096)
    event_write_limit_per_minute: int = Field(default=120, ge=1, le=100_000)
    command_write_limit_per_minute: int = Field(default=30, ge=1, le=100_000)
    worker_concurrency: int = Field(default=4, ge=1, le=64)
    worker_poll_seconds: float = Field(default=1.0, ge=0.1, le=60)

    def build_event_cipher(self) -> EventCipher | None:
        primary = _read_event_key(
            self.event_encryption_key, self.event_encryption_key_file,
            "MEMORY_EVENT_ENCRYPTION_KEY",
        )
        if primary is None:
            return None
        fallback = _read_event_key(
            self.event_encryption_fallback_key, self.event_encryption_fallback_key_file,
            "MEMORY_EVENT_ENCRYPTION_FALLBACK_KEY",
        )
        return EventCipher(primary, fallback)

    @model_validator(mode="after")
    def validate_security_mode(self) -> "Settings":
        has_primary = (
            self.event_encryption_key is not None or self.event_encryption_key_file is not None
        )
        has_fallback = (
            self.event_encryption_fallback_key is not None
            or self.event_encryption_fallback_key_file is not None
        )
        if (has_fallback or self.event_encryption_rotate) and not has_primary:
            raise ValueError("event encryption rotation requires a primary key")
        if self.event_encryption_rotate and not has_fallback:
            raise ValueError("event encryption rotation requires a fallback key")
        if self.environment == "production":
            if self.dev_jwt_secret is not None:
                raise ValueError("development JWT secret is forbidden in production")
            if not self.jwks_url:
                raise ValueError("JWKS URL is required in production")
            for name, url in (
                ("MEMORY_DATABASE_URL", self.database_url),
                ("MEMORY_WORKER_DATABASE_URL", self.worker_database_url),
            ):
                if url is not None:
                    require_verified_database_tls(url.get_secret_value(), name)
            if not has_primary:
                raise ValueError("MEMORY_EVENT_ENCRYPTION_KEY is required in production")
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
        if self.embedding_model and (not self.model_base_url or self.embedding_dimensions is None):
            raise ValueError("embedding requires model_base_url and embedding_dimensions")
        if self.rerank_model and not self.model_base_url:
            raise ValueError("reranking requires model_base_url")
        return self


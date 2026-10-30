from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import HTTPException

from memory_system.api import JwtAuthenticator
from memory_system.dev import issue_dev_token
from memory_system.settings import Settings


@pytest.mark.asyncio
async def test_development_token_is_usable_but_cannot_be_enabled_in_production() -> None:
    secret = "development-only-secret-at-least-32-characters"
    settings = Settings(
        _env_file=None,
        environment="development",
        database_url="postgresql+psycopg://unused/unused",
        jwt_issuer="persistent-memory-dev",
        jwt_audience="persistent-memory",
        dev_jwt_secret=secret,
    )
    token = issue_dev_token(settings, "tenant-a", "user-a", "agent-a")
    authenticator = JwtAuthenticator(
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        jwks_url=None,
        dev_secret=secret,
        environment="development",
    )

    identity = await authenticator.authenticate_token(token)

    assert identity.tenant_id == "tenant-a"
    assert identity.allowed_purposes == {"conversation", "task", "history", "support"}
    assert identity.memory_write is True

    with pytest.raises(ValueError, match="development JWT secret"):
        Settings(
            _env_file=None,
            environment="production",
            database_url="postgresql+psycopg://unused/unused",
            jwt_issuer="https://issuer.example.com/",
            jwt_audience="persistent-memory",
            jwks_url="https://issuer.example.com/.well-known/jwks.json",
            dev_jwt_secret=secret,
        )


def test_shadow_mode_requires_model_configuration() -> None:
    base = {
        "_env_file": None,
        "environment": "development",
        "database_url": "postgresql+psycopg://unused/unused",
        "jwt_issuer": "persistent-memory-dev",
        "jwt_audience": "persistent-memory",
        "dev_jwt_secret": "development-only-secret-at-least-32-characters",
        "extraction_mode": "shadow",
    }

    with pytest.raises(ValueError, match="model extraction"):
        Settings(**base)

    settings = Settings(
        **base,
        model_base_url="https://model.example.com/v1",
        extraction_model="memory-extractor",
    )

    assert settings.extraction_mode == "shadow"


@pytest.mark.asyncio
async def test_jwt_rejects_string_memory_write_claim() -> None:
    secret = "development-only-secret-at-least-32-characters"
    authenticator = JwtAuthenticator(
        issuer="persistent-memory-dev",
        audience="persistent-memory",
        jwks_url=None,
        dev_secret=secret,
        environment="development",
    )
    token = jwt.encode(
        {
            "iss": "persistent-memory-dev",
            "aud": "persistent-memory",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "agent_id": "agent-a",
            "memory_purposes": ["conversation"],
            "memory_write": "false",
        },
        secret,
        algorithm="HS256",
    )

    with pytest.raises(HTTPException) as error:
        await authenticator.authenticate_token(token)

    assert error.value.status_code == 401

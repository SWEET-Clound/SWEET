import base64
import json
from datetime import UTC, datetime, timedelta
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.request import HTTPSHandler
from urllib.response import addinfourl
from uuid import uuid4

import jwt
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from pydantic import SecretStr

from memory_system.api import JwtAuthenticator
from memory_system.dev import issue_dev_token, migrate_main
from memory_system.event_cipher import EventCipher
from memory_system.settings import Settings


def test_production_migration_rejects_unverified_database_url(monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_ENVIRONMENT", "production")
    monkeypatch.setenv("MEMORY_DATABASE_URL", "postgresql+psycopg://unused/memory?sslmode=require")
    with pytest.raises(ValueError, match="migration database URL must specify sslmode=verify-full"):
        migrate_main()


def test_production_migration_accepts_url_encoded_tls_options(monkeypatch, capsys) -> None:
    monkeypatch.setenv("MEMORY_ENVIRONMENT", "production")
    monkeypatch.setenv(
        "MEMORY_DATABASE_URL",
        "postgresql+psycopg://owner:p%25ss@unused/memory"
        "?sslmode=verify-full&sslrootcert=%2Fcerts%2Froot%20ca.pem",
    )
    config = Config()
    migrations = Path(__file__).resolve().parents[1] / "migrations"
    config.set_main_option("script_location", str(migrations))

    command.upgrade(config, "head", sql=True)

    assert "CREATE TABLE memory_event" in capsys.readouterr().out


@pytest.mark.parametrize("sslmode", ["require", "verify-full&sslmode=disable", "verify-full"])
def test_production_database_requires_full_tls_verification(sslmode: str) -> None:
    options = {
        "_env_file": None,
        "environment": "production",
        "database_url": f"postgresql+psycopg://db.test/memory?sslmode={sslmode}",
        "jwt_issuer": "issuer",
        "jwt_audience": "memory-api",
        "jwks_url": "https://issuer.test/jwks",
        "event_encryption_key": base64.b64encode(bytes(32)).decode(),
    }
    if sslmode == "verify-full":
        assert Settings(**options).build_event_cipher() is not None
    else:
        with pytest.raises(ValueError, match="MEMORY_DATABASE_URL must specify sslmode=verify-full"):
            Settings(**options)


@pytest.mark.parametrize("key", [None, "", "not-base64"])
def test_production_rejects_missing_or_invalid_event_encryption_key(key: str | None) -> None:
    with pytest.raises(ValueError, match="MEMORY_EVENT_ENCRYPTION_KEY|event encryption key"):
        Settings(
            _env_file=None,
            environment="production",
            database_url="postgresql+psycopg://db.test/memory?sslmode=verify-full",
            jwt_issuer="issuer",
            jwt_audience="memory-api",
            jwks_url="https://issuer.test/jwks",
            event_encryption_key=key,
        ).build_event_cipher()


def test_production_loads_mounted_event_keys(tmp_path: Path, monkeypatch) -> None:
    primary = base64.b64encode(bytes(32)).decode()
    fallback = base64.b64encode(bytes([1]) * 32).decode()
    primary_path = tmp_path / "primary"
    fallback_path = tmp_path / "fallback"
    primary_path.write_text(primary + "\n", encoding="ascii")
    fallback_path.write_text(fallback + "\n", encoding="ascii")
    monkeypatch.setenv("MEMORY_EVENT_ENCRYPTION_KEY_FILE", str(primary_path))
    monkeypatch.setenv("MEMORY_EVENT_ENCRYPTION_FALLBACK_KEY_FILE", str(fallback_path))
    settings = Settings(
        _env_file=None,
        environment="production",
        database_url="postgresql+psycopg://db.test/memory?sslmode=verify-full",
        jwt_issuer="issuer",
        jwt_audience="memory-api",
        jwks_url="https://issuer.test/jwks",
        event_encryption_rotate=True,
    )
    cipher = settings.build_event_cipher()
    assert cipher is not None and cipher.key_id == EventCipher(primary).key_id
    event_id = str(uuid4())
    old_payload = EventCipher(fallback).encrypt(event_id, "旧正文", {})
    assert cipher.decrypt(event_id, old_payload) == {"content": "旧正文", "metadata": {}}
    with pytest.raises(ValueError, match="configure only one"):
        settings.model_copy(update={"event_encryption_key": SecretStr(primary)}).build_event_cipher()
    fallback_path.unlink()
    with pytest.raises(ValueError, match="cannot read MEMORY_EVENT_ENCRYPTION_FALLBACK_KEY_FILE"):
        settings.build_event_cipher()


@pytest.mark.parametrize("url", ["http://issuer.test/jwks", "https:///jwks"])
def test_production_rejects_insecure_or_hostless_jwks_url(url: str) -> None:
    with pytest.raises(ValueError, match="production JWKS URL must use HTTPS"):
        JwtAuthenticator("issuer", "memory-api", url)


@pytest.mark.parametrize("location", ["http://issuer.test/keys", "https://other.test/keys"])
def test_jwks_transport_rejects_redirects(monkeypatch, location: str) -> None:
    url = "https://issuer.test/jwks"

    def redirect(_handler, request):
        assert request.full_url == url
        headers = Message()
        headers["Location"] = location
        response = addinfourl(BytesIO(b""), headers, url, 302)
        response.msg = "Found"
        return response

    monkeypatch.setattr(HTTPSHandler, "https_open", redirect)
    authenticator = JwtAuthenticator("issuer", "memory-api", url)
    assert authenticator.jwks is not None
    with pytest.raises(jwt.exceptions.PyJWKClientConnectionError, match="302"):
        authenticator.jwks.fetch_data()


@pytest.mark.asyncio
async def test_production_jwks_verifies_signature_and_audience(monkeypatch) -> None:
    trusted = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    untrusted = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(trusted.public_key()))
    jwk.update(kid="memory-key", use="sig", alg="RS256")
    authenticator = JwtAuthenticator(
        issuer="memory-issuer", audience="memory-api", jwks_url="https://issuer.test/jwks"
    )
    assert authenticator.jwks is not None
    monkeypatch.setattr(authenticator.jwks, "fetch_data", lambda: {"keys": [jwk]})
    claims = {
        "iss": "memory-issuer",
        "aud": "memory-api",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "agent_id": "agent-a",
    }

    def token(key, **overrides):
        return jwt.encode(claims | overrides, key, algorithm="RS256", headers={"kid": "memory-key"})

    assert (await authenticator.authenticate_token(token(trusted))).user_id == "user-a"
    for invalid in (token(untrusted), token(trusted, aud="different-api")):
        with pytest.raises(HTTPException) as exc:
            await authenticator.authenticate_token(invalid)
        assert exc.value.status_code == 401

    def unavailable():
        raise jwt.exceptions.PyJWKClientConnectionError("test connection failure")

    assert authenticator.jwks.jwk_set_cache is not None
    authenticator.jwks.jwk_set_cache.put(None)
    monkeypatch.setattr(authenticator.jwks, "fetch_data", unavailable)
    with pytest.raises(HTTPException) as exc:
        await authenticator.authenticate_token(token(trusted))
    assert exc.value.status_code == 503
    assert exc.value.detail == "jwks_unavailable"


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
    assert identity.allowed_purposes == {
        "conversation",
        "task",
        "history",
        "support",
        "review",
        "audit",
    }
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

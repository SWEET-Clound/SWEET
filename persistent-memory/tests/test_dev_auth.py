import json
from datetime import UTC, datetime, timedelta
from email.message import Message
from io import BytesIO
from urllib.request import HTTPSHandler
from urllib.response import addinfourl

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from memory_system.api import JwtAuthenticator
from memory_system.dev import issue_dev_token
from memory_system.settings import Settings


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

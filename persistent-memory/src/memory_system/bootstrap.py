from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from memory_system.api import JwtAuthenticator, create_app
from memory_system.embedding import build_embedding_client
from memory_system.policy import MemoryPolicy
from memory_system.postgres import PostgresRepository
from memory_system.rerank import RerankClient
from memory_system.settings import Settings
from memory_system.system import MemorySystem


def create_application() -> FastAPI:
    settings = Settings()  # type: ignore[call-arg]
    repository = PostgresRepository.from_url(
        settings.database_url.get_secret_value(),
        event_write_limit_per_minute=settings.event_write_limit_per_minute,
        command_write_limit_per_minute=settings.command_write_limit_per_minute,
        event_cipher=settings.build_event_cipher(),
    )
    authenticate = JwtAuthenticator(
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        jwks_url=settings.jwks_url,
        dev_secret=(
            settings.dev_jwt_secret.get_secret_value()
            if settings.dev_jwt_secret is not None
            else None
        ),
        environment=settings.environment,
    )
    embedding_client = build_embedding_client(settings)
    rerank_client = None
    if settings.rerank_model and settings.model_base_url:
        key = settings.model_api_key
        rerank_client = RerankClient(
            httpx.AsyncClient(
                base_url=settings.model_base_url.rstrip("/") + "/",
                headers={"Authorization": f"Bearer {key.get_secret_value()}"} if key else None,
                timeout=settings.model_timeout_seconds,
            ),
            settings.rerank_model,
        )
    system = MemorySystem(
        repository, MemoryPolicy(), settings.max_context_tokens, embedding_client, rerank_client
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if embedding_client is not None:
                embedding_client.http.close()
            if rerank_client is not None:
                await rerank_client.http.aclose()
            await repository.close()

    return create_app(system, authenticate, lifespan)


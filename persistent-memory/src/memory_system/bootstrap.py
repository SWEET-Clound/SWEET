from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from memory_system.api import JwtAuthenticator, create_app
from memory_system.policy import MemoryPolicy
from memory_system.postgres import PostgresRepository
from memory_system.settings import Settings
from memory_system.system import MemorySystem


def create_application() -> FastAPI:
    settings = Settings()  # type: ignore[call-arg]
    repository = PostgresRepository.from_url(settings.database_url.get_secret_value())
    system = MemorySystem(repository, MemoryPolicy(), settings.max_context_tokens)
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

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await repository.close()

    return create_app(system, authenticate, lifespan)


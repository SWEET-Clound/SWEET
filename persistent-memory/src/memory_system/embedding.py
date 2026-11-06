"""Validate vectors at the boundary of an OpenAI-compatible embedding gateway."""

from __future__ import annotations

import math

import httpx
from pydantic import BaseModel, Field

from memory_system.settings import Settings


def build_embedding_client(settings: Settings) -> EmbeddingClient | None:
    if not settings.embedding_model or settings.embedding_dimensions is None:
        return None
    key = settings.model_api_key
    http = httpx.Client(
        base_url=(settings.model_base_url or "").rstrip("/") + "/",
        headers={"Authorization": f"Bearer {key.get_secret_value()}"} if key else None,
        timeout=settings.model_timeout_seconds,
    )
    return EmbeddingClient(http, settings.embedding_model, settings.embedding_dimensions)


class _EmbeddingRow(BaseModel):
    index: int = Field(ge=0)
    embedding: list[float]


class _EmbeddingResponse(BaseModel):
    data: list[_EmbeddingRow]


class EmbeddingClient:
    def __init__(self, http: httpx.Client, model: str, dimensions: int) -> None:
        if dimensions < 1:
            raise ValueError("embedding dimensions must be positive")
        self.http = http
        self.model = model
        self.dimensions = dimensions

    def embed(self, inputs: list[str]) -> list[list[float]]:
        if not inputs:
            return []
        response = self.http.post("embeddings", json={"model": self.model, "input": inputs})
        rows = sorted(
            _EmbeddingResponse.model_validate_json(response.raise_for_status().content).data,
            key=lambda row: row.index,
        )
        if [row.index for row in rows] != list(range(len(inputs))):
            raise ValueError("embedding response indexes do not match the inputs")
        vectors = [row.embedding for row in rows]
        if any(
            len(vector) != self.dimensions
            or not all(math.isfinite(value) for value in vector)
            or not any(vector)
            for vector in vectors
        ):
            raise ValueError("embedding response contains an invalid vector")
        return vectors

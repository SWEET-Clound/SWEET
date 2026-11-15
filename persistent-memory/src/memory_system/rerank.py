import httpx
from pydantic import BaseModel, Field


class _RankedDocument(BaseModel):
    index: int = Field(strict=True, ge=0)


class _RerankResponse(BaseModel):
    results: list[_RankedDocument] = Field(max_length=16)


class RerankClient:
    def __init__(self, http: httpx.AsyncClient, model: str) -> None:
        self.http = http
        self.model = model

    async def rank(self, query: str, documents: list[str]) -> list[int]:
        async with self.http.stream(
            "POST", "rerank",
            json={"model": self.model, "query": query, "documents": documents},
        ) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=8192):
                if len(body) + len(chunk) > 64 * 1024:
                    raise ValueError("rerank response exceeded the byte limit")
                body.extend(chunk)
        result = _RerankResponse.model_validate_json(body)
        indexes = [document.index for document in result.results]
        if sorted(indexes) != list(range(len(documents))):
            raise ValueError("rerank response must contain each document index exactly once")
        return indexes

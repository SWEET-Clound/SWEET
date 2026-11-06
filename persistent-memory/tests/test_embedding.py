import json

import httpx
import pytest

from memory_system.embedding import EmbeddingClient


@pytest.mark.parametrize(
    ("index", "vector", "valid"),
    [(1, [0, 1], True), (0, [0, 1], False), (1, [1], False), (1, [0, 0], False)],
)
def test_embedding_preserves_input_order_and_checks_vectors(index, vector, valid) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        assert json.loads(request.content) == {"model": "test-model", "input": ["甲", "乙"]}
        return httpx.Response(200, json={"data": [
            {"index": index, "embedding": vector},
            {"index": 0, "embedding": [1, 0]},
        ]})

    with httpx.Client(base_url="http://test/v1/", transport=httpx.MockTransport(handle)) as http:
        client = EmbeddingClient(http, "test-model", 2)
        if valid:
            assert client.embed(["甲", "乙"]) == [[1, 0], [0, 1]]
        else:
            with pytest.raises(ValueError, match="embedding response"):
                client.embed(["甲", "乙"])

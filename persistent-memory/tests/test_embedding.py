import json
import math

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


def test_embedding_stops_reading_oversized_stream() -> None:
    class OversizedStream(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            for _ in range(65):
                yield b" " * (64 * 1024)
            raise AssertionError("client continued consuming an oversized response")

        def close(self):
            self.closed = True

    stream = OversizedStream()
    transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    with (
        httpx.Client(base_url="http://test/", transport=transport) as http,
        pytest.raises(ValueError, match="exceeded the byte limit"),
    ):
        EmbeddingClient(http, "test-model", 2).embed(["甲"])
    assert stream.closed


@pytest.mark.parametrize("scale", [1e300, 1e-300])
def test_embedding_normalizes_extreme_finite_vectors(scale: float) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [3 * scale, 4 * scale]}]}
        )
    )
    with httpx.Client(base_url="http://test/", transport=transport) as http:
        vector, = EmbeddingClient(http, "test-model", 2).embed(["甲"])
    assert vector == pytest.approx([0.6, 0.8])
    assert math.hypot(*vector) == pytest.approx(1)

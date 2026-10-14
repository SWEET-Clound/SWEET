"""供应商无关的中文流式 TTS 文本分片器。"""


class SentenceChunker:
    """尽量按中文标点输出完整语义片段，不修改原始文本。"""

    _BOUNDARIES = frozenset("。！？；，!?;,\n")

    def __init__(self, max_chars: int = 80) -> None:
        if max_chars <= 0:
            raise ValueError("max_chars 必须大于 0")
        self._max_chars = max_chars
        self._buffer = ""

    def add(self, text: str) -> tuple[str, ...]:
        if not isinstance(text, str):
            raise TypeError("TTS 文本必须是字符串")
        if not text:
            return ()
        chunks: list[str] = []
        for char in text:
            self._buffer += char
            if char in self._BOUNDARIES or len(self._buffer) >= self._max_chars:
                chunks.append(self._take_buffer())
        return tuple(chunks)

    def flush(self) -> str:
        return self._take_buffer()

    def reset(self) -> None:
        self._buffer = ""

    def _take_buffer(self) -> str:
        chunk, self._buffer = self._buffer, ""
        return chunk

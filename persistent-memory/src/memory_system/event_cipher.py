import base64
import binascii
import hashlib
import json
import os
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _decode_key(encoded_key: str) -> bytes:
    try:
        key = base64.b64decode(encoded_key, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("event encryption key must be base64-encoded") from None
    if len(key) != 32:
        raise ValueError("event encryption key must decode to 32 bytes")
    return key


class EventCipher:
    def __init__(self, encoded_key: str, fallback_encoded_key: str | None = None) -> None:
        key = _decode_key(encoded_key)
        self.key_id = hashlib.sha256(key).hexdigest()[:32]
        self.aead = AESGCM(key)
        self.fallback = (
            AESGCM(_decode_key(fallback_encoded_key))
            if fallback_encoded_key is not None else None
        )

    def encrypt(self, event_id: str, content: str, metadata: dict[str, Any]) -> bytes:
        nonce = os.urandom(12)
        payload = json.dumps({"content": content, "metadata": metadata}, ensure_ascii=False)
        return nonce + self.aead.encrypt(nonce, payload.encode(), UUID(event_id).bytes)

    def decrypt(self, event_id: str, ciphertext: bytes) -> dict[str, Any]:
        args = ciphertext[:12], ciphertext[12:], UUID(event_id).bytes
        try:
            payload = self.aead.decrypt(*args)
        except InvalidTag:
            if self.fallback is None:
                raise
            payload = self.fallback.decrypt(*args)
        return json.loads(payload)

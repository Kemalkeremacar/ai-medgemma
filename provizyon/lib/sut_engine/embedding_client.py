from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

from .settings import (
    DEFAULT_EMBEDDING_BATCH_SIZE,
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_EMBEDDING_TIMEOUT,
    DEFAULT_TEI_BASE_URL,
)


@dataclass
class EmbeddingConfig:
    base_url: str = DEFAULT_TEI_BASE_URL
    dim: int = DEFAULT_EMBEDDING_DIM
    timeout: int = DEFAULT_EMBEDDING_TIMEOUT
    batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE


class EmbeddingClient:
    """TEI /embed endpoint'i için dependency-light embedding istemcisi."""

    def __init__(self, config: EmbeddingConfig):
        self.config = config
        self.base_url = config.base_url.rstrip("/")

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        cleaned = [text.strip() for text in texts if text and text.strip()]
        if not cleaned:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(cleaned), self.config.batch_size):
            batch = cleaned[start : start + self.config.batch_size]
            vectors.extend(self._embed_chunk(batch))
        return vectors

    def embed_one(self, text: str) -> list[float]:
        vectors = self.embed_batch([text])
        if not vectors:
            raise RuntimeError("Boş metin embed edilemez.")
        return vectors[0]

    def _embed_chunk(self, texts: list[str]) -> list[list[float]]:
        payload = json.dumps({"inputs": texts}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))

        if not isinstance(data, list) or not data:
            raise RuntimeError(f"TEI yanıt formatı beklenmedik: {type(data)}")
        if len(data[0]) != self.config.dim:
            raise RuntimeError(
                f"Beklenmedik embedding boyutu: {len(data[0])}; beklenen {self.config.dim}"
            )
        return data

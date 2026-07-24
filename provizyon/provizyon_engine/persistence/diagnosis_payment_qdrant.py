"""Tanı-işlem geçmiş ödeme eğilimi sinyallerini Qdrant ``diagnosis_procedure_pilot``
collection'ından okur (TEI embedding + vektör araması).

Vektör araması yalnızca aday getirmek içindir; kesin eşleşme (kurum + tanı kökü +
işlem kod/ad) çağıran katmanda Python post-filter ile uygulanır.
"""

from __future__ import annotations

from typing import Any

from .. import _sut_bootstrap  # noqa: F401
from .. import settings


class DiagnosisPaymentSignalReader:
    """``diagnosis_procedure_pilot`` collection'ından aday sinyal payload'larını döner."""

    def __init__(
        self,
        *,
        qdrant_url: str | None = None,
        tei_url: str | None = None,
        collection: str | None = None,
        dim: int | None = None,
    ) -> None:
        self.qdrant_url = qdrant_url or settings.QDRANT_URL
        self.tei_url = tei_url or settings.TEI_URL
        self.collection = collection or settings.DIAGNOSIS_PROCEDURE_COLLECTION
        self.dim = dim or settings.EMBEDDING_DIM
        self._client = None
        self._embedder_obj = None

    def _qdrant(self):
        if self._client is None:
            from qdrant_client import QdrantClient

            self._client = QdrantClient(
                url=self.qdrant_url, timeout=60, check_compatibility=False
            )
        return self._client

    def _embed(self, text: str) -> list[float]:
        if self._embedder_obj is None:
            from sut_engine.embedding_client import EmbeddingClient, EmbeddingConfig

            self._embedder_obj = EmbeddingClient(
                EmbeddingConfig(base_url=self.tei_url, dim=self.dim)
            )
        return self._embedder_obj.embed_one(text)

    def ping(self) -> bool:
        try:
            self._qdrant().get_collection(self.collection)
            return True
        except Exception:
            return False

    def search_candidates(self, query_text: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Sorgu metnini embed edip vektör benzerliğiyle aday payload'ları döner."""

        if not query_text or not query_text.strip():
            return []

        vector = self._embed(query_text)
        client = self._qdrant()
        response = client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=limit,
            with_payload=True,
        )
        raw_points = response.points if hasattr(response, "points") else response
        candidates: list[dict[str, Any]] = []
        for point in raw_points:
            payload = getattr(point, "payload", None) or {}
            if not isinstance(payload, dict):
                continue
            enriched = dict(payload)
            enriched["_score"] = float(getattr(point, "score", 0.0) or 0.0)
            candidates.append(enriched)
        return candidates

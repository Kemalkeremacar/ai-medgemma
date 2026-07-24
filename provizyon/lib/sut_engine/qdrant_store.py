from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .settings import (
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_QDRANT_COLLECTION,
    DEFAULT_QDRANT_TIMEOUT,
    DEFAULT_QDRANT_URL,
)


@dataclass
class QdrantConfig:
    url: str = DEFAULT_QDRANT_URL
    collection: str = DEFAULT_QDRANT_COLLECTION
    vector_dim: int = DEFAULT_EMBEDDING_DIM
    timeout: int = DEFAULT_QDRANT_TIMEOUT


def stable_point_id(value: str) -> str:
    digest = hashlib.md5(value.encode("utf-8")).hexdigest()
    return f"{digest[0:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


class SUTQdrantStore:
    """Sadece SUT `sut_knowledge` collection'ı için Qdrant wrapper."""

    def __init__(self, config: QdrantConfig):
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, PointStruct, VectorParams
        except ImportError as exc:
            raise RuntimeError(
                "qdrant-client paketi kurulu değil. Kurmak için: python -m pip install qdrant-client"
            ) from exc

        self.config = config
        self.client = QdrantClient(
            url=config.url,
            timeout=config.timeout,
            check_compatibility=False,
        )
        self.Distance = Distance
        self.PointStruct = PointStruct
        self.VectorParams = VectorParams

    def ensure_collection(self) -> None:
        existing = {item.name for item in self.client.get_collections().collections}
        if self.config.collection in existing:
            return

        self.client.create_collection(
            collection_name=self.config.collection,
            vectors_config=self.VectorParams(
                size=self.config.vector_dim,
                distance=self.Distance.COSINE,
            ),
        )
        for field in (
            "source_kind",
            "source_list",
            "code",
            "rule_types",
            "analysis_status",
            "pipeline_run_id",
        ):
            try:
                self.client.create_payload_index(
                    collection_name=self.config.collection,
                    field_name=field,
                    field_schema="keyword",
                )
            except Exception:
                pass

    def upsert(self, point_id: str, vector: list[float], payload: dict) -> None:
        point = self.PointStruct(
            id=point_id,
            vector=vector,
            payload=payload,
        )
        self.client.upsert(
            collection_name=self.config.collection,
            points=[point],
            wait=True,
        )

    def search(self, vector: list[float], limit: int = 10, query_filter=None) -> list[dict]:
        response = self.client.query_points(
            collection_name=self.config.collection,
            query=vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        points = response.points if hasattr(response, "points") else response
        return [
            {
                "score": point.score,
                "payload": point.payload or {},
            }
            for point in points
        ]

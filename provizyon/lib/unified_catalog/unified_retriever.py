from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LIB_ROOT = Path(__file__).resolve().parent.parent
_PROVIZYON_ROOT = _LIB_ROOT.parent

if str(_LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(_LIB_ROOT))

from sut_engine.embedding_client import EmbeddingClient, EmbeddingConfig
from sut_engine.qdrant_store import QdrantConfig, SUTQdrantStore
from sut_engine.settings import DEFAULT_QDRANT_URL, DEFAULT_TEI_BASE_URL

from unified_catalog.backfill_unified_qdrant import DEFAULT_COLLECTION
from unified_catalog.io_utils import read_jsonl
from unified_catalog.normalization import (
    extract_huv_codes,
    extract_sut_codes,
    fold,
    score_ratio,
    tokens,
)


DEFAULT_OUT_DIR = _PROVIZYON_ROOT / "data" / "generated" / "unified_catalog_final_medgemma"


@dataclass
class RetrievedEntry:
    row: dict[str, Any]
    score: float = 0.0
    source: str = "local"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "retrieval_source": self.source,
            "warnings": self.warnings,
            **self.row,
        }


class UnifiedCatalogRetriever:
    def __init__(
        self,
        *,
        out_dir: Path = DEFAULT_OUT_DIR,
        use_qdrant: bool = True,
        collection: str = DEFAULT_COLLECTION,
        qdrant_url: str = DEFAULT_QDRANT_URL,
        tei_url: str = DEFAULT_TEI_BASE_URL,
        context_limit: int = 8,
    ):
        self.out_dir = out_dir
        self.collection = collection
        self.context_limit = context_limit
        self.crosswalk = read_jsonl(out_dir / "huv_sut_crosswalk.jsonl")
        self.unified = read_jsonl(out_dir / "unified_catalog.jsonl")
        self.note_rules = read_jsonl(out_dir / "huv_note_rules.jsonl")
        self.by_huv: dict[str, list[dict[str, Any]]] = {}
        self.by_sut: dict[str, list[dict[str, Any]]] = {}
        for row in self.crosswalk:
            huv_code = str(row.get("huv_code") or "")
            sut_code = str(row.get("sut_code") or "").upper()
            if huv_code:
                self.by_huv.setdefault(huv_code, []).append(row)
            if sut_code:
                self.by_sut.setdefault(sut_code, []).append(row)

        self.embedder: EmbeddingClient | None = None
        self.store: SUTQdrantStore | None = None
        self.qdrant_error: str | None = None
        if use_qdrant:
            try:
                self.embedder = EmbeddingClient(EmbeddingConfig(base_url=tei_url))
                self.store = SUTQdrantStore(
                    QdrantConfig(
                        url=qdrant_url,
                        collection=collection,
                    )
                )
            except Exception as exc:
                self.qdrant_error = str(exc)

    def retrieve(self, query: str, *, limit: int | None = None) -> list[RetrievedEntry]:
        limit = limit or self.context_limit
        results: list[RetrievedEntry] = []
        seen: set[str] = set()

        for huv_code in extract_huv_codes(query):
            for row in self.by_huv.get(huv_code, []):
                self._add(results, seen, row, 1.0, "exact_huv")
        huv_suffixes = {code.split(".", 1)[1] for code in extract_huv_codes(query) if "." in code}
        for sut_code in extract_sut_codes(query):
            if sut_code in huv_suffixes:
                continue
            for row in self.by_sut.get(sut_code.upper(), []):
                self._add(results, seen, row, 1.0, "exact_sut")

        if len(results) >= limit:
            return results[:limit]

        if self.embedder and self.store:
            try:
                vector = self.embedder.embed_one(query)
                qdrant_results = self.store.search(vector, limit=limit)
                for result in qdrant_results:
                    payload = result.get("payload") or {}
                    row = _payload_to_row(payload)
                    self._add(
                        results,
                        seen,
                        row,
                        float(result.get("score") or 0.0),
                        "qdrant_unified",
                    )
            except Exception as exc:
                self.qdrant_error = str(exc)

        if len(results) < limit:
            for entry in self._local_search(query, limit=limit):
                self._add(results, seen, entry.row, entry.score, entry.source)

        return results[:limit]

    def note_rules_for_huv(self, huv_code: str) -> list[dict[str, Any]]:
        return [
            row for row in self.note_rules
            if str(row.get("huv_code") or "") == huv_code
        ]

    def _add(
        self,
        results: list[RetrievedEntry],
        seen: set[str],
        row: dict[str, Any],
        score: float,
        source: str,
    ) -> None:
        key = str(row.get("canonical_id") or f"{row.get('huv_code')}::{row.get('sut_code')}::{row.get('relation_type')}")
        if key in seen:
            return
        seen.add(key)
        results.append(
            RetrievedEntry(
                row=row,
                score=score,
                source=source,
                warnings=_row_warnings(row),
            )
        )

    def _local_search(self, query: str, *, limit: int) -> list[RetrievedEntry]:
        query_tokens = tokens(query)
        folded_query = fold(query)
        scored: list[RetrievedEntry] = []
        for row in self.crosswalk:
            text = " ".join(
                str(row.get(key) or "")
                for key in (
                    "huv_code",
                    "huv_name",
                    "sut_code",
                    "sut_name",
                    "huv_section",
                    "huv_top_title",
                    "reason",
                    "review_reason",
                )
            )
            row_tokens = tokens(text)
            overlap = len(query_tokens & row_tokens)
            ratio = max(
                score_ratio(query, str(row.get("huv_name") or "")),
                score_ratio(query, str(row.get("sut_name") or "")),
            )
            score = overlap * 10.0 + ratio * 10.0
            if folded_query and folded_query in fold(text):
                score += 30.0
            if score <= 0:
                continue
            scored.append(
                RetrievedEntry(
                    row=row,
                    score=round(score, 4),
                    source="local_fallback",
                    warnings=_row_warnings(row),
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]


def _payload_to_row(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "canonical_id": payload.get("canonical_id"),
        "huv_code": payload.get("huv_code"),
        "huv_name": payload.get("huv_name"),
        "sut_code": payload.get("sut_code"),
        "sut_name": payload.get("sut_name"),
        "relation_type": payload.get("relation_type"),
        "confidence": payload.get("confidence"),
        "confidence_score": payload.get("confidence_score"),
        "decision_source": payload.get("decision_source"),
        "reason": payload.get("reason"),
        "review_recommended": payload.get("review_recommended"),
        "review_reason": payload.get("review_reason"),
        "huv_section": payload.get("huv_section"),
        "huv_top_title": payload.get("huv_top_title"),
        "huv_note": payload.get("huv_note"),
        "sut_source_list": payload.get("sut_source_list"),
        "sut_rule_count": payload.get("sut_rule_count"),
        "level1_domain": payload.get("level1_domain"),
        "level2_specialty": payload.get("level2_specialty"),
        "level3_service_group": payload.get("level3_service_group"),
        "level4_procedure_family": payload.get("level4_procedure_family"),
        "level5_variant": payload.get("level5_variant"),
        "alternatives": payload.get("alternatives") or [],
    }


def _row_warnings(row: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if not row.get("sut_code"):
        warnings.append("SUT karşılığı yok veya boş")
    if row.get("review_recommended"):
        warnings.append(str(row.get("review_reason") or "İnceleme öneriliyor"))
    if row.get("confidence") in {"low", "medium"}:
        warnings.append(f"Güven etiketi {row.get('confidence')}")
    if row.get("relation_type") in {"needs_review", "huv_only", "included_service"}:
        warnings.append(f"İlişki tipi {row.get('relation_type')}")
    return [warning for warning in warnings if warning]

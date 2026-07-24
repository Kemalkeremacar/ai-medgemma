from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

_LIB_ROOT = Path(__file__).resolve().parent.parent
_PROVIZYON_ROOT = _LIB_ROOT.parent

if str(_LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(_LIB_ROOT))

from sut_engine.embedding_client import EmbeddingClient, EmbeddingConfig
from sut_engine.qdrant_store import QdrantConfig, SUTQdrantStore, stable_point_id
from sut_engine.settings import DEFAULT_QDRANT_URL, DEFAULT_TEI_BASE_URL

from unified_catalog.io_utils import read_jsonl


DEFAULT_OUT_DIR = _PROVIZYON_ROOT / "data" / "generated" / "unified_catalog_final_medgemma"
DEFAULT_COLLECTION = "huv_sut_unified_catalog"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Final HUV-SUT birleşik kataloğunu Qdrant'a yazar.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--qdrant-url", default=DEFAULT_QDRANT_URL)
    parser.add_argument("--tei-url", default=DEFAULT_TEI_BASE_URL)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--reset-collection", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def run_backfill(
    *,
    out_dir: Path = DEFAULT_OUT_DIR,
    collection: str = DEFAULT_COLLECTION,
    qdrant_url: str = DEFAULT_QDRANT_URL,
    tei_url: str = DEFAULT_TEI_BASE_URL,
    offset: int = 0,
    limit: int | None = None,
    batch_size: int = 16,
    reset_collection: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    rows = _combined_rows(out_dir)
    selected = rows[offset:]
    if limit is not None:
        selected = selected[:limit]

    summary: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "out_dir": str(out_dir),
        "collection": collection,
        "qdrant_url": qdrant_url,
        "tei_url": tei_url,
        "total_rows": len(rows),
        "selected_rows": len(selected),
        "offset": offset,
        "limit": limit,
        "batch_size": batch_size,
        "reset_collection": reset_collection,
        "dry_run": dry_run,
        "sample": [
            {
                "point_id": item["point_id"],
                "huv_code": item["payload"].get("huv_code"),
                "huv_name": item["payload"].get("huv_name"),
                "sut_code": item["payload"].get("sut_code"),
                "sut_name": item["payload"].get("sut_name"),
                "review_recommended": item["payload"].get("review_recommended"),
                "text": item["text"][:500],
            }
            for item in selected[:5]
        ],
    }
    if dry_run:
        return summary

    embedder = EmbeddingClient(
        EmbeddingConfig(
            base_url=tei_url,
            batch_size=batch_size,
        )
    )
    store = SUTQdrantStore(
        QdrantConfig(
            url=qdrant_url,
            collection=collection,
        )
    )
    if reset_collection:
        _delete_collection_if_exists(store, collection)
    store.ensure_collection()
    _ensure_payload_indexes(store, collection)

    processed = 0
    for chunk in _chunks(selected, batch_size):
        texts = [item["text"] for item in chunk]
        vectors = embedder.embed_batch(texts)
        if len(vectors) != len(chunk):
            raise RuntimeError(f"Embedding sayısı beklenenden farklı: {len(vectors)} != {len(chunk)}")
        points = [
            store.PointStruct(
                id=item["point_id"],
                vector=vector,
                payload=item["payload"],
            )
            for item, vector in zip(chunk, vectors, strict=True)
        ]
        store.client.upsert(
            collection_name=collection,
            points=points,
            wait=True,
        )
        processed += len(chunk)
        print(f"Qdrant upsert: {processed}/{len(selected)}")

    summary["processed_rows"] = processed
    summary["completed_at"] = datetime.now().isoformat(timespec="seconds")
    (out_dir / "unified_qdrant_backfill_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def _combined_rows(out_dir: Path) -> list[dict[str, Any]]:
    crosswalk_rows = read_jsonl(out_dir / "huv_sut_crosswalk.jsonl")
    unified_rows = read_jsonl(out_dir / "unified_catalog.jsonl")
    crosswalk_by_id = {
        str(row.get("canonical_id") or f"service::{row.get('huv_code') or 'no-huv'}::{row.get('sut_code') or 'no-sut'}"): row
        for row in crosswalk_rows
    }
    rows: list[dict[str, Any]] = []
    for unified in unified_rows:
        canonical_id = str(
            unified.get("canonical_id")
            or f"service::{unified.get('huv_code') or 'no-huv'}::{unified.get('sut_code') or 'no-sut'}"
        )
        crosswalk = crosswalk_by_id.get(canonical_id, {})
        payload = _payload(canonical_id, unified, crosswalk)
        text = _embedding_text(payload)
        rows.append(
            {
                "point_id": stable_point_id(f"huv-sut-unified:{canonical_id}"),
                "payload": {**payload, "text": text},
                "text": text,
            }
        )
    return rows


def _payload(canonical_id: str, unified: dict[str, Any], crosswalk: dict[str, Any]) -> dict[str, Any]:
    def get(key: str, default: Any = "") -> Any:
        return unified.get(key, crosswalk.get(key, default))

    return {
        "source_kind": "huv_sut_unified_catalog",
        "canonical_id": canonical_id,
        "huv_code": get("huv_code"),
        "huv_name": get("huv_name"),
        "sut_code": get("sut_code"),
        "sut_name": get("sut_name"),
        "relation_type": get("relation_type"),
        "confidence": get("confidence"),
        "confidence_score": get("confidence_score", 0.0),
        "decision_source": get("decision_source"),
        "reason": get("reason"),
        "review_recommended": bool(get("review_recommended", False)),
        "review_reason": get("review_reason"),
        "huv_section": get("huv_section"),
        "huv_top_title": get("huv_top_title"),
        "huv_note": get("huv_note"),
        "sut_source_list": get("sut_source_list"),
        "sut_rule_count": get("sut_rule_count", 0),
        "level1_domain": get("level1_domain"),
        "level2_specialty": get("level2_specialty"),
        "level3_service_group": get("level3_service_group"),
        "level4_procedure_family": get("level4_procedure_family"),
        "level5_variant": get("level5_variant"),
        "alternatives": (crosswalk.get("alternatives") or [])[:3],
    }


def _embedding_text(payload: dict[str, Any]) -> str:
    parts = [
        f"HUV kodu: {payload.get('huv_code')}",
        f"HUV işlem adı: {payload.get('huv_name')}",
        f"SUT kodu: {payload.get('sut_code') or 'yok'}",
        f"SUT işlem adı: {payload.get('sut_name') or 'yok'}",
        f"İlişki tipi: {payload.get('relation_type')}",
        f"Güven: {payload.get('confidence')} {payload.get('confidence_score')}",
        f"İnceleme önerisi: {payload.get('review_recommended')} {payload.get('review_reason') or ''}",
        f"Taksonomi: {payload.get('level1_domain')} > {payload.get('level2_specialty')} > {payload.get('level3_service_group')} > {payload.get('level4_procedure_family')} > {payload.get('level5_variant')}",
        f"HUV bölüm: {payload.get('huv_section')} {payload.get('huv_top_title')}",
        f"HUV not: {payload.get('huv_note') or ''}",
        f"Gerekçe: {payload.get('reason') or ''}",
    ]
    alternatives = payload.get("alternatives") or []
    if alternatives:
        parts.append("Alternatif SUT adayları: " + "; ".join(
            _alternative_text(item) for item in alternatives[:3]
        ))
    return "\n".join(str(part) for part in parts if str(part).strip())


def _alternative_text(item: Any) -> str:
    if isinstance(item, dict):
        return " ".join(
            str(part)
            for part in (item.get("sut_code"), item.get("sut_name"), item.get("why_not_primary"))
            if part
        )
    return str(item)


def _delete_collection_if_exists(store: SUTQdrantStore, collection: str) -> None:
    existing = {item.name for item in store.client.get_collections().collections}
    if collection in existing:
        store.client.delete_collection(collection_name=collection)


def _ensure_payload_indexes(store: SUTQdrantStore, collection: str) -> None:
    for field in (
        "source_kind",
        "canonical_id",
        "huv_code",
        "sut_code",
        "relation_type",
        "confidence",
        "decision_source",
        "review_recommended",
        "level1_domain",
        "level2_specialty",
        "level3_service_group",
    ):
        try:
            store.client.create_payload_index(
                collection_name=collection,
                field_name=field,
                field_schema="keyword",
            )
        except Exception:
            pass


def _chunks(items: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    size = max(1, size)
    for start in range(0, len(items), size):
        yield items[start : start + size]


def main() -> int:
    args = parse_args()
    summary = run_backfill(
        out_dir=Path(args.out_dir),
        collection=args.collection,
        qdrant_url=args.qdrant_url,
        tei_url=args.tei_url,
        offset=args.offset,
        limit=args.limit,
        batch_size=args.batch_size,
        reset_collection=args.reset_collection,
        dry_run=args.dry_run,
    )
    print("Unified Qdrant backfill özeti:")
    for key, value in summary.items():
        print(f"- {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

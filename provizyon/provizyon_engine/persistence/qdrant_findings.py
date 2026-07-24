"""Karar katmanlarını Qdrant ``patient_findings`` collection'ına yazar/okur.

(karar sırası.txt Adım 9 - sonuçları vektör DB / Qdrant'a yazma)

Saklanan karar katmanları (txt'deki "saklanacak bilgi türleri"):
  1. İşlem-tanı uyumu (tani_kurali + sut_kurali)
  2. Belge-hasta uyumu
  3. Evrak gerekliliği
  4. AI / MedGemma yorumu
  5. Nihai provizyon kararı

ÖNEMLİ: Belge yanlış hastaya aitse (yanlis_hasta_belgesi) içerik RAG'e
yazılmaz; yalnızca nihai karar kaydı tutulur (``allow_document_rag=False``).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .. import _sut_bootstrap  # noqa: F401
from .. import settings
from ..models import JobResult


def _point_id(value: str) -> str:
    digest = hashlib.md5(value.encode("utf-8")).hexdigest()
    return f"{digest[0:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


_PAYLOAD_INDEX_FIELDS = ("provizyon_id", "hasta_id", "tc_kimlik", "layer", "nihai_karar")
_DEPRECATED_LAYERS = ("belge_islem",)


@dataclass
class PatientFindingLayer:
    layer: str
    message: str = ""
    status: str | None = None
    gerekce: str | None = None
    guven: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "message": self.message,
            "status": self.status,
            "gerekce": self.gerekce,
            "guven": self.guven,
        }


@dataclass
class PatientProvizyonRecord:
    provizyon_id: str
    hasta_id: str | None = None
    tc_kimlik: str | None = None
    nihai_karar: str = ""
    finished_at: str | None = None
    layers: list[PatientFindingLayer] = field(default_factory=list)
    score: float | None = None
    source: str = "history"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provizyon_id": self.provizyon_id,
            "hasta_id": self.hasta_id,
            "tc_kimlik": self.tc_kimlik,
            "nihai_karar": self.nihai_karar,
            "finished_at": self.finished_at,
            "layers": [layer.to_dict() for layer in self.layers],
            "score": self.score,
            "source": self.source,
        }


def group_points_into_records(
    points: list[Any],
    *,
    source: str = "history",
    scores: dict[str, float] | None = None,
    exclude_provizyon_id: str | None = None,
    limit: int | None = None,
) -> list[PatientProvizyonRecord]:
    """Qdrant noktalarını provizyon bazında gruplar."""

    grouped: dict[str, PatientProvizyonRecord] = {}
    for point in points:
        payload = getattr(point, "payload", None) or point.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        provizyon_id = str(payload.get("provizyon_id") or "").strip()
        if not provizyon_id or provizyon_id == exclude_provizyon_id:
            continue
        layer_name = str(payload.get("layer") or "").strip()
        if not layer_name:
            continue

        rec = grouped.get(provizyon_id)
        if rec is None:
            rec = PatientProvizyonRecord(
                provizyon_id=provizyon_id,
                hasta_id=payload.get("hasta_id"),
                tc_kimlik=payload.get("tc_kimlik"),
                nihai_karar=str(payload.get("nihai_karar") or ""),
                finished_at=payload.get("finished_at"),
                source=source,
                score=(scores or {}).get(provizyon_id),
            )
            grouped[provizyon_id] = rec

        rec.layers.append(
            PatientFindingLayer(
                layer=layer_name,
                message=str(payload.get("message") or payload.get("gerekce") or ""),
                status=payload.get("status"),
                gerekce=payload.get("gerekce"),
                guven=payload.get("guven"),
            )
        )
        if payload.get("nihai_karar"):
            rec.nihai_karar = str(payload.get("nihai_karar"))
        if payload.get("finished_at"):
            rec.finished_at = str(payload.get("finished_at"))
        if payload.get("hasta_id"):
            rec.hasta_id = str(payload.get("hasta_id"))
        if payload.get("tc_kimlik"):
            rec.tc_kimlik = str(payload.get("tc_kimlik"))

    records = list(grouped.values())
    records.sort(key=lambda item: item.finished_at or "", reverse=True)
    if limit is not None:
        records = records[:limit]
    return records


class _FindingsStoreBase:
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
        self.collection = collection or settings.PATIENT_FINDINGS_COLLECTION
        self.dim = dim or settings.EMBEDDING_DIM
        self._client = None
        self._embedder_obj = None
        self._ensured = False

    def _qdrant(self):
        if self._client is None:
            from qdrant_client import QdrantClient

            self._client = QdrantClient(url=self.qdrant_url, timeout=60, check_compatibility=False)
        return self._client

    def _embed(self, text: str) -> list[float]:
        if self._embedder_obj is None:
            from sut_engine.embedding_client import EmbeddingClient, EmbeddingConfig

            self._embedder_obj = EmbeddingClient(
                EmbeddingConfig(base_url=self.tei_url, dim=self.dim)
            )
        return self._embedder_obj.embed_one(text)

    def _ensure_collection(self) -> None:
        if self._ensured:
            return
        from qdrant_client.models import Distance, VectorParams

        client = self._qdrant()
        existing = {c.name for c in client.get_collections().collections}
        if self.collection not in existing:
            client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=self.dim, distance=Distance.COSINE),
            )
        for field in _PAYLOAD_INDEX_FIELDS:
            try:
                client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema="keyword",
                )
            except Exception:
                pass
        self._ensured = True

    def ping(self) -> bool:
        try:
            self._qdrant().get_collections()
            return True
        except Exception:
            return False


class PatientFindingsWriter(_FindingsStoreBase):
    """``patient_findings`` collection'ına embedding'li karar kaydı yazar."""

    def write(
        self,
        result: JobResult,
        *,
        tc_kimlik: str | None = None,
        allow_document_rag: bool = True,
        institution_name: str | None = None,
        facility_level: str | None = None,
        yas: int | None = None,
        cinsiyet: str | None = None,
    ) -> dict[str, Any]:
        """Karar katmanlarını yazar. Yazılan nokta sayısını/durumu döner."""

        from qdrant_client.models import PointStruct

        self._ensure_collection()
        client = self._qdrant()

        layers = self._collect_layers(
            result,
            tc_kimlik=tc_kimlik,
            allow_document_rag=allow_document_rag,
            institution_name=institution_name,
            facility_level=facility_level,
            yas=yas,
            cinsiyet=cinsiyet,
        )
        points = []
        errors: list[str] = []
        for layer_name, text, payload in layers:
            try:
                vector = self._embed(text)
            except Exception as exc:
                errors.append(f"{layer_name}: embed hatası: {exc}")
                continue
            pid = _point_id(f"{result.provizyon_id}:{layer_name}")
            points.append(PointStruct(id=pid, vector=vector, payload=payload))

        if points:
            client.upsert(collection_name=self.collection, points=points, wait=True)
            self._delete_deprecated_layers(client, result.provizyon_id)

        return {"written": len(points), "errors": errors, "rag_allowed": allow_document_rag}

    def _delete_deprecated_layers(self, client, provizyon_id: str) -> None:
        from qdrant_client.models import PointIdsList

        ids = [_point_id(f"{provizyon_id}:{layer}") for layer in _DEPRECATED_LAYERS]
        if not ids:
            return
        try:
            client.delete(
                collection_name=self.collection,
                points_selector=PointIdsList(points=ids),
                wait=True,
            )
        except Exception:
            pass

    @staticmethod
    def _yas_grubu(yas: int | None) -> str | None:
        if yas is None:
            return None
        if yas < 18:
            return "pediatrik"
        if yas >= 65:
            return "geriatrik"
        return "erişkin"

    def _collect_layers(
        self,
        result: JobResult,
        *,
        tc_kimlik: str | None,
        allow_document_rag: bool,
        institution_name: str | None = None,
        facility_level: str | None = None,
        yas: int | None = None,
        cinsiyet: str | None = None,
    ) -> list[tuple[str, str, dict[str, Any]]]:
        base = {
            "provizyon_id": result.provizyon_id,
            "hasta_id": result.hasta_id,
            "tc_kimlik": tc_kimlik,
            "nihai_karar": result.nihai_karar.value,
            "finished_at": result.finished_at,
            "institution_name": institution_name,
            "facility_level": facility_level,
            "yas_grubu": self._yas_grubu(yas),
            "cinsiyet": cinsiyet,
        }
        out: list[tuple[str, str, dict[str, Any]]] = []

        out.append(
            (
                "nihai_karar",
                f"Provizyon {result.provizyon_id} nihai karar: {result.nihai_karar.value}. {result.gerekce}",
                {**base, "layer": "nihai_karar", "gerekce": result.gerekce},
            )
        )

        if not allow_document_rag:
            return out

        if result.belge_hasta:
            out.append(
                (
                    "belge_hasta",
                    f"Belge-hasta uyumu: {result.belge_hasta.message}",
                    {
                        **base,
                        "layer": "belge_hasta",
                        "status": result.belge_hasta.status.value,
                        "message": result.belge_hasta.message,
                    },
                )
            )
        if result.zorunlu_evrak:
            out.append(
                (
                    "zorunlu_evrak",
                    f"Evrak gerekliliği: {result.zorunlu_evrak.message}",
                    {
                        **base,
                        "layer": "zorunlu_evrak",
                        "status": result.zorunlu_evrak.status.value,
                        "message": result.zorunlu_evrak.message,
                    },
                )
            )
        if result.tani_kurali:
            out.append(
                (
                    "tani_kurali",
                    f"İşlem-tanı uyumu: {result.tani_kurali.message}",
                    {
                        **base,
                        "layer": "tani_kurali",
                        "status": result.tani_kurali.status.value,
                        "message": result.tani_kurali.message,
                    },
                )
            )
        if result.sut_tani_kurali:
            out.append(
                (
                    "sut_tani_kurali",
                    f"SUT işlem-tanı uyumu: {result.sut_tani_kurali.message}",
                    {
                        **base,
                        "layer": "sut_tani_kurali",
                        "status": result.sut_tani_kurali.status.value,
                        "message": result.sut_tani_kurali.message,
                    },
                )
            )
        if result.sut_kurali:
            out.append(
                (
                    "sut_kurali",
                    f"SUT işlem kuralı: {result.sut_kurali.message}",
                    {
                        **base,
                        "layer": "sut_kurali",
                        "status": result.sut_kurali.status.value,
                        "message": result.sut_kurali.message,
                    },
                )
            )
        if result.medgemma:
            out.append(
                (
                    "medgemma",
                    f"MedGemma klinik yorumu: {result.medgemma.gerekce}",
                    {
                        **base,
                        "layer": "medgemma",
                        "gerekce": result.medgemma.gerekce,
                        "guven": result.medgemma.guven,
                        "manuel_inceleme_gerekli": result.medgemma.manuel_inceleme_gerekli,
                    },
                )
            )
        return out


class PatientFindingsReader(_FindingsStoreBase):
    """``patient_findings`` collection'ından hasta geçmişi ve benzer vakaları okur."""

    def _patient_filter(
        self,
        *,
        hasta_id: str | None,
        tc_kimlik: str | None,
        exclude_provizyon_id: str | None = None,
    ):
        """TC öncelikli hasta filtresi: TC varsa sadece TC ile ara, yoksa hasta_id fallback."""
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        must: list[FieldCondition] = []
        if tc_kimlik:
            must.append(FieldCondition(key="tc_kimlik", match=MatchValue(value=tc_kimlik)))
        elif hasta_id:
            must.append(FieldCondition(key="hasta_id", match=MatchValue(value=hasta_id)))
        else:
            return None

        must_not = []
        if exclude_provizyon_id:
            must_not.append(
                FieldCondition(key="provizyon_id", match=MatchValue(value=exclude_provizyon_id))
            )
        return Filter(must=must, must_not=must_not or None)

    def fetch_by_patient(
        self,
        *,
        hasta_id: str | None = None,
        tc_kimlik: str | None = None,
        exclude_provizyon_id: str | None = None,
        limit: int = 5,
    ) -> list[PatientProvizyonRecord]:
        query_filter = self._patient_filter(
            hasta_id=hasta_id,
            tc_kimlik=tc_kimlik,
            exclude_provizyon_id=exclude_provizyon_id,
        )
        if query_filter is None:
            return []

        self._ensure_collection()
        client = self._qdrant()
        points: list[Any] = []
        offset = None
        while len(points) < limit * 8:
            batch, offset = client.scroll(
                collection_name=self.collection,
                scroll_filter=query_filter,
                limit=64,
                offset=offset,
                with_payload=True,
            )
            if not batch:
                break
            points.extend(batch)
            if offset is None:
                break

        return group_points_into_records(
            points,
            source="history",
            exclude_provizyon_id=exclude_provizyon_id,
            limit=limit,
        )

    def fetch_similar(
        self,
        query_text: str,
        *,
        exclude_provizyon_id: str | None = None,
        limit: int = 3,
    ) -> list[PatientProvizyonRecord]:
        if not query_text.strip():
            return []

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        self._ensure_collection()
        client = self._qdrant()
        vector = self._embed(query_text)

        must_not = []
        if exclude_provizyon_id:
            must_not.append(
                FieldCondition(key="provizyon_id", match=MatchValue(value=exclude_provizyon_id))
            )
        query_filter = Filter(must_not=must_not or None)

        response = client.query_points(
            collection_name=self.collection,
            query=vector,
            query_filter=query_filter,
            limit=max(limit * 6, 12),
            with_payload=True,
        )
        raw_points = response.points if hasattr(response, "points") else response
        scores: dict[str, float] = {}
        for point in raw_points:
            payload = point.payload or {}
            provizyon_id = str(payload.get("provizyon_id") or "").strip()
            if not provizyon_id:
                continue
            score = float(getattr(point, "score", 0.0) or 0.0)
            prev = scores.get(provizyon_id)
            if prev is None or score > prev:
                scores[provizyon_id] = score

        return group_points_into_records(
            raw_points,
            source="similar",
            scores=scores,
            exclude_provizyon_id=exclude_provizyon_id,
            limit=limit,
        )

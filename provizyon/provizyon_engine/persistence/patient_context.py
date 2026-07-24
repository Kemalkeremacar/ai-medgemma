"""Hasta geçmişi ve benzer vaka bağlamı (Qdrant patient_findings okuma)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import settings
from ..models import ProvizyonJob
from .qdrant_findings import PatientFindingsReader, PatientProvizyonRecord


@dataclass
class PatientContext:
    """Aynı hastanın geçmiş kayıtları + opsiyonel benzer vakalar."""

    history: list[PatientProvizyonRecord] = field(default_factory=list)
    similar: list[PatientProvizyonRecord] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def has_context(self) -> bool:
        return bool(self.history or self.similar)

    def to_dict(self) -> dict[str, Any]:
        return {
            "history_count": len(self.history),
            "similar_count": len(self.similar),
            "history": [record.to_dict() for record in self.history],
            "similar": [record.to_dict() for record in self.similar],
            "notes": list(self.notes),
        }


def _yas_grubu(yas: int | None) -> str:
    if yas is None:
        return "bilinmiyor"
    if yas < 18:
        return "pediatrik"
    if yas >= 65:
        return "geriatrik"
    return "erişkin"


def _build_similar_query(job: ProvizyonJob) -> str:
    parts = [
        f"HUV: {', '.join(job.all_huv_codes()) or '-'}",
        f"ICD: {', '.join(job.diagnoses) or '-'}",
    ]
    if job.procedures:
        names = [proc.name for proc in job.procedures if proc.name]
        if names:
            parts.append(f"İşlemler: {', '.join(names)}")
    parts.append(f"Yaş grubu: {_yas_grubu(job.yas)}")
    if job.cinsiyet and job.cinsiyet.value != "bilinmiyor":
        parts.append(f"Cinsiyet: {job.cinsiyet.value}")
    if job.facility_level:
        parts.append(f"Kurum tipi: {job.facility_level}")
    return ". ".join(parts)


def load_patient_context(
    job: ProvizyonJob,
    *,
    reader: PatientFindingsReader | None = None,
    enable_similar: bool | None = None,
) -> PatientContext | None:
    """hasta_id / tc_kimlik ile geçmiş kayıtları ve opsiyonel benzer vakaları yükler."""

    if not settings.ENABLE_PATIENT_CONTEXT:
        return None
    if not job.hasta_id and not job.tc_kimlik:
        return None

    reader = reader or PatientFindingsReader()
    ctx = PatientContext()
    errors: list[str] = []

    try:
        ctx.history = reader.fetch_by_patient(
            hasta_id=job.hasta_id,
            tc_kimlik=job.tc_kimlik,
            exclude_provizyon_id=job.provizyon_id,
            limit=settings.PATIENT_CONTEXT_MAX_RECORDS,
        )
    except Exception as exc:
        errors.append(f"Geçmiş hasta kaydı okunamadı: {exc}")

    use_similar = (
        settings.ENABLE_SIMILAR_CASES if enable_similar is None else enable_similar
    )
    if use_similar:
        try:
            seen = {record.provizyon_id for record in ctx.history}
            similar = reader.fetch_similar(
                _build_similar_query(job),
                exclude_provizyon_id=job.provizyon_id,
                limit=settings.PATIENT_CONTEXT_SIMILAR_LIMIT,
            )
            ctx.similar = [record for record in similar if record.provizyon_id not in seen]
        except Exception as exc:
            errors.append(f"Benzer vaka araması başarısız: {exc}")

    if errors:
        ctx.notes.extend(errors)
    if not ctx.has_context and not ctx.notes:
        return None
    return ctx


def format_patient_context_for_prompt(ctx: PatientContext | None) -> str:
    """MedGemma prompt'una eklenecek geçmiş bağlam metni."""

    if ctx is None or not ctx.has_context:
        return ""

    lines: list[str] = []
    if ctx.history:
        lines.append("GEÇMİŞ PROVİZYON KAYITLARI (aynı hasta):")
        for record in ctx.history:
            lines.append(_format_record(record))

    if ctx.similar:
        lines.append("")
        lines.append("BENZER VAKA KAYITLARI (farklı hasta, klinik benzerlik):")
        for record in ctx.similar:
            score = f" skor={record.score:.3f}" if record.score is not None else ""
            lines.append(_format_record(record, prefix=score))

    lines.append("")
    lines.append(
        "Bu geçmiş kayıtlar yalnızca bağlam içindir; mevcut belgeler ve kurallar önceliklidir."
    )
    return "\n".join(lines)


def _format_record(record: PatientProvizyonRecord, *, prefix: str = "") -> str:
    layer_bits: list[str] = []
    for layer in record.layers:
        if layer.layer == "nihai_karar":
            continue
        bit = layer.status or layer.layer
        if layer.message:
            bit = f"{layer.layer}={bit}"
        layer_bits.append(bit)
    layer_summary = ", ".join(layer_bits) if layer_bits else "-"
    when = record.finished_at or "tarih bilinmiyor"
    hasta = record.hasta_id or record.tc_kimlik or "-"
    return (
        f"- Provizyon {record.provizyon_id}{prefix} | hasta={hasta} | "
        f"{when} | karar={record.nihai_karar} | katmanlar: {layer_summary}"
    )

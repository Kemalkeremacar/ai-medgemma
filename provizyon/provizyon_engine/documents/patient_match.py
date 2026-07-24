"""Belge-hasta uyumu (karar sırası.txt Adım 2/3 - önce hasta/belge doğruluğu).

Bir belge başka bir hastaya aitse diğer değerlendirmelerin anlamı kalmaz:
sonuç doğrudan ``yanlis_hasta_belgesi`` olur ve belge RAG'e yazılmaz.

Kontrol stratejisi (deterministik, açıklanabilir):
1. Belge meta verisinde beyan edilen hasta_id varsa ve provizyondaki ile
   uyuşmuyorsa -> kesin uyumsuz.
2. Provizyondaki hasta adı/hasta_id/TC belge metninde geçiyorsa -> uyumlu.
3. Metin çıkmayan veya kısa belgeler -> exempt (aggregation'da sayılmaz).
4. Hiçbiri doğrulanamıyorsa -> belirsiz (uncertain).
5. Aggregation: en az 1 match + geri kalan uncertain/exempt -> PASS (warning ile).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import LayerResult, LayerStatus, ProvizyonJob
from .extract import ExtractedDocument

_TR_MAP = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")

_SHORT_CORPUS_THRESHOLD = 200


def _fold(value: str | None) -> str:
    if not value:
        return ""
    text = value.translate(_TR_MAP).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text)).strip()


def _name_tokens(name: str | None) -> list[str]:
    folded = _fold(name)
    return [tok for tok in folded.split(" ") if len(tok) >= 2]


def _tc_in_corpus(tc: str | None, corpus: str) -> bool:
    """TC kimlik numarasını (11 hane) belge corpus'unda arar."""
    if not tc or len(tc) != 11 or not tc.isdigit():
        return False
    return tc in corpus


@dataclass
class DocumentMatch:
    title: str
    verdict: str  # match | mismatch | uncertain | exempt
    reason: str
    detail: dict = field(default_factory=dict)


def match_documents(job: ProvizyonJob, extracted: list[ExtractedDocument]) -> LayerResult:
    """Tüm belgeleri provizyon hastasıyla karşılaştırır."""

    usable = [doc for doc in extracted if doc.ref.exists and not doc.error]
    if not usable:
        return LayerResult(
            layer="belge_hasta",
            status=LayerStatus.SKIPPED,
            message="Karşılaştırılacak okunabilir belge yok.",
        )

    job_hasta_id = (job.hasta_id or "").strip()
    job_tc = (job.tc_kimlik or "").strip()
    job_name_tokens = _name_tokens(job.patient_name)
    matches: list[DocumentMatch] = []

    for doc in usable:
        title = doc.ref.title or doc.ref.doc_type or doc.ref.path.name
        corpus = _fold(doc.combined_text)
        raw_text = doc.combined_text or ""

        # 1) Beyan edilen hasta_id çelişkisi.
        declared_id = (doc.ref.declared_hasta_id or "").strip()
        if declared_id and job_hasta_id and declared_id != job_hasta_id:
            matches.append(
                DocumentMatch(
                    title=title,
                    verdict="mismatch",
                    reason=f"Belge beyan edilen hasta_id ({declared_id}) provizyon hastası ({job_hasta_id}) ile uyuşmuyor.",
                    detail={"declared_hasta_id": declared_id, "job_hasta_id": job_hasta_id},
                )
            )
            continue

        declared_name_tokens = _name_tokens(doc.ref.declared_patient_name)
        if declared_name_tokens and job_name_tokens:
            overlap = set(declared_name_tokens) & set(job_name_tokens)
            if not overlap:
                matches.append(
                    DocumentMatch(
                        title=title,
                        verdict="mismatch",
                        reason="Belgede beyan edilen hasta adı provizyon hastasıyla örtüşmüyor.",
                        detail={
                            "declared_name": doc.ref.declared_patient_name,
                            "job_name": job.patient_name,
                        },
                    )
                )
                continue

        # 2) Metin içinde hasta kimliği doğrulaması.
        id_found = bool(job_hasta_id) and _fold(job_hasta_id) in corpus
        tc_found = _tc_in_corpus(job_tc, raw_text)
        name_hits = [tok for tok in job_name_tokens if tok in corpus]
        name_found = len(name_hits) >= 2 or (len(job_name_tokens) == 1 and len(name_hits) == 1)

        if id_found or tc_found or name_found:
            matches.append(
                DocumentMatch(
                    title=title,
                    verdict="match",
                    reason="Belge metni provizyon hastasıyla doğrulandı.",
                    detail={"id_found": id_found, "tc_found": tc_found, "name_hits": name_hits},
                )
            )
            continue

        # 3) Metin çıkmayan veya çok kısa belgeler -> exempt.
        if not corpus or len(corpus) < _SHORT_CORPUS_THRESHOLD:
            matches.append(
                DocumentMatch(
                    title=title,
                    verdict="exempt",
                    reason="Belge metni çok kısa veya çıkarılamadı; eşleşme beklenmez.",
                    detail={"corpus_len": len(corpus)},
                )
            )
            continue

        # 4) Hasta bilgisi hiç sağlanmadıysa eşleştirme yapılamaz.
        if not job_hasta_id and not job_tc and not job_name_tokens:
            matches.append(
                DocumentMatch(
                    title=title,
                    verdict="uncertain",
                    reason="Provizyonda hasta adı/ID/TC yok; belge-hasta uyumu doğrulanamadı.",
                    detail={"corpus_len": len(corpus), "corpus_preview": corpus[:100]},
                )
            )
            continue

        matches.append(
            DocumentMatch(
                title=title,
                verdict="uncertain",
                reason="Belge metninde provizyon hastası açıkça bulunamadı.",
                detail={
                    "name_hits": name_hits,
                    "id_found": id_found,
                    "tc_found": tc_found,
                    "corpus_len": len(corpus),
                    "corpus_preview": corpus[:100],
                },
            )
        )

    return _aggregate(matches)


def _aggregate(matches: list[DocumentMatch]) -> LayerResult:
    verdicts = [m.verdict for m in matches]
    detail = {"documents": [m.__dict__ for m in matches]}

    if "mismatch" in verdicts:
        mismatched = [m for m in matches if m.verdict == "mismatch"]
        return LayerResult(
            layer="belge_hasta",
            status=LayerStatus.FAIL,
            message="; ".join(m.reason for m in mismatched),
            detail=detail,
        )

    significant = [v for v in verdicts if v not in ("exempt",)]
    has_match = "match" in significant
    has_uncertain = "uncertain" in significant

    if has_match and not has_uncertain:
        return LayerResult(
            layer="belge_hasta",
            status=LayerStatus.PASS,
            message="Tüm belgeler provizyon hastasıyla uyumlu.",
            detail=detail,
        )
    if has_match and has_uncertain:
        uncertain_titles = [m.title for m in matches if m.verdict == "uncertain"]
        return LayerResult(
            layer="belge_hasta",
            status=LayerStatus.PASS,
            message=f"Ana belge(ler) doğrulandı. Doğrulanamayan ek belgeler: {', '.join(uncertain_titles)}",
            detail=detail,
        )
    if not has_match and not has_uncertain:
        return LayerResult(
            layer="belge_hasta",
            status=LayerStatus.PASS,
            message="Tüm belgeler kısa/metinsiz; eşleşme beklenmez.",
            detail=detail,
        )
    return LayerResult(
        layer="belge_hasta",
        status=LayerStatus.REVIEW,
        message="Belge-hasta uyumu otomatik doğrulanamadı; manuel kontrol gerekli.",
        detail=detail,
    )

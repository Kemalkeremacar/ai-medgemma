"""MedGemma kanıt paketi hazırlama (karar sırası.txt Adım 3 son adım).

Büyük/çok sayfalı belgeleri MedGemma'ya uygun hale getirir:
- Görselleri ``VISION_MAX_EDGE_PX`` sınırına küçültür.
- ``VISION_MAX_IMAGES=0`` ise tüm sayfa render'ları ve gömülü görseller gönderilir.
- Metin kanıtını ilgililik sırasına göre birleştirir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import settings
from .classify import classify_evidence_role
from .extract import ExtractedDocument, _is_garbled_text

_NON_CLINICAL_ROLES = frozenset({"fatura_hizmet", "hasta_dogrulama"})

_CLINICAL_KEYWORDS = re.compile(
    r"epikriz|rapor|te[sş]his|ameliyat|operasyon|patoloji|biyopsi|"
    r"radyoloji|konsültasyon|endoskopi|ultrason|tomografi|mr\b|bt\b|pet\b|"
    r"odyometri|ekokardiyografi|anjiyografi|doppler|stres\s+test",
    re.IGNORECASE,
)

_DOC_TYPE_BOOSTS: dict[str, float] = {
    "epikriz": 0.35,
    "ameliyat": 0.3,
    "rapor": 0.25,
    "odyometri": 0.35,
    "ekokardiyografi": 0.35,
    "radyoloji": 0.3,
    "patoloji": 0.3,
    "konsultasyon": 0.25,
    "muayene": 0.2,
    "kimlik": 0.05,
}


@dataclass
class EvidencePackage:
    """MedGemma'ya gidecek kanıt paketi."""

    text_evidence: str = ""
    image_paths: list[Path] = field(default_factory=list)
    document_titles: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    selected_page_numbers: list[int] = field(default_factory=list)
    excluded_page_numbers: list[int] = field(default_factory=list)
    partial_vision: bool = False

    @property
    def has_images(self) -> bool:
        return len(self.image_paths) > 0

    @property
    def has_text(self) -> bool:
        return bool(self.text_evidence.strip())


@dataclass
class _ScoredPage:
    page_number: int
    doc_index: int
    page_in_doc: int
    doc_title: str
    image_path: Path
    needs_ocr: bool
    relevance_score: float
    text: str = ""
    pinned: bool = False


def _page_visual_paths(page: PageContent) -> list[Path]:
    """Sayfa başına tek görsel — render + embedded çift sayımını önler."""

    if page.embedded_image_paths:
        existing = [p for p in page.embedded_image_paths if p.exists()]
        if existing:
            return [max(existing, key=lambda p: p.stat().st_size)]
    if page.image_path is not None and page.image_path.exists():
        return [page.image_path]
    if page.image_path is not None:
        return [page.image_path]
    return []


def _resize_image(src: Path, max_edge: int) -> Path:
    """Görseli en uzun kenar ``max_edge`` olacak şekilde küçültür."""

    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return src
    try:
        with Image.open(str(src)) as img:
            width, height = img.size
            longest = max(width, height)
            if longest <= max_edge:
                return src
            scale = max_edge / float(longest)
            new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            resized = img.convert("RGB").resize(new_size)
            out_path = src.with_name(f"{src.stem}_v{max_edge}.jpg")
            resized.save(str(out_path), format="JPEG", quality=85)
            return out_path
    except Exception:
        return src


def _compute_relevance(text: str, keywords: list[str]) -> float:
    """Sayfa metnine ilgililik skoru hesaplar (0.0 - 2.0 arası)."""
    if not text:
        return 0.0
    score = 0.0
    text_lower = text.lower()

    for kw in keywords:
        if kw.lower() in text_lower:
            score += 0.25

    clinical_matches = _CLINICAL_KEYWORDS.findall(text_lower)
    score += min(len(clinical_matches) * 0.1, 0.4)

    return min(score, 2.0)


def _doc_type_boost(title: str, doc_type: str | None) -> float:
    folded = f"{title} {doc_type or ''}".lower()
    boost = 0.0
    for token, value in _DOC_TYPE_BOOSTS.items():
        if token in folded:
            boost = max(boost, value)
    return boost


def _build_relevance_keywords(
    huv_codes: list[str] | None = None,
    sut_codes: list[str] | None = None,
    icd_codes: list[str] | None = None,
    patient_name: str | None = None,
    procedure_names: list[str] | None = None,
    extra_keywords: list[str] | None = None,
) -> list[str]:
    keywords: list[str] = []
    if huv_codes:
        for code in huv_codes:
            keywords.append(code.strip())
            parts = code.strip().split(".")
            if len(parts) >= 2:
                keywords.append(parts[-1])
                keywords.append("".join(parts))
    if sut_codes:
        for code in sut_codes:
            keywords.append(code.strip())
    if icd_codes:
        for code in icd_codes:
            keywords.append(code.strip())
    if patient_name:
        for tok in patient_name.strip().split():
            if len(tok) >= 2:
                keywords.append(tok)
    if procedure_names:
        for name in procedure_names:
            for tok in re.split(r"[\s,/\-()]+", name):
                tok = tok.strip()
                if len(tok) >= 4:
                    keywords.append(tok)
    if extra_keywords:
        keywords.extend(kw.strip() for kw in extra_keywords if kw and kw.strip())
    return keywords


def _pin_document_edges(pages: list[_ScoredPage]) -> None:
    by_doc: dict[int, list[_ScoredPage]] = {}
    for page in pages:
        by_doc.setdefault(page.doc_index, []).append(page)
    for doc_pages in by_doc.values():
        ordered = sorted(doc_pages, key=lambda p: p.page_in_doc)
        if not ordered:
            continue
        first = ordered[0]
        first.pinned = True
        first.relevance_score += 0.5
        if len(ordered) > 1:
            last = ordered[-1]
            if last.page_number != first.page_number:
                last.pinned = True
                last.relevance_score += 0.4


def _select_pages(pages: list[_ScoredPage], max_images: int) -> tuple[list[_ScoredPage], list[int]]:
    if max_images <= 0 or len(pages) <= max_images:
        return sorted(pages, key=lambda p: p.page_number), []

    _pin_document_edges(pages)
    by_doc = {page.doc_index for page in pages}
    max_per_doc = max(2, min(3, max_images // max(len(by_doc), 1) + 1))

    selected: list[_ScoredPage] = []
    selected_numbers: set[int] = set()
    doc_counts: dict[int, int] = {}

    for page in sorted(
        [p for p in pages if p.pinned],
        key=lambda p: p.relevance_score,
        reverse=True,
    ):
        if page.page_number in selected_numbers:
            continue
        selected.append(page)
        selected_numbers.add(page.page_number)
        doc_counts[page.doc_index] = doc_counts.get(page.doc_index, 0) + 1

    remaining = sorted(
        [p for p in pages if p.page_number not in selected_numbers],
        key=lambda p: p.relevance_score,
        reverse=True,
    )
    for page in remaining:
        if len(selected) >= max_images:
            break
        if doc_counts.get(page.doc_index, 0) >= max_per_doc:
            continue
        selected.append(page)
        selected_numbers.add(page.page_number)
        doc_counts[page.doc_index] = doc_counts.get(page.doc_index, 0) + 1

    if len(selected) < max_images:
        for page in remaining:
            if len(selected) >= max_images:
                break
            if page.page_number in selected_numbers:
                continue
            selected.append(page)
            selected_numbers.add(page.page_number)

    selected.sort(key=lambda p: p.page_number)
    excluded = sorted(p.page_number for p in pages if p.page_number not in selected_numbers)
    return selected, excluded


def _build_text_evidence(
    extracted: list[ExtractedDocument],
    keywords: list[str],
    *,
    max_chars: int,
) -> str:
    scored_chunks: list[tuple[float, str]] = []
    for doc in extracted:
        if not doc.ref.exists or doc.error:
            continue
        title = doc.ref.title or doc.ref.doc_type or doc.ref.path.name
        for page in doc.pages:
            text = (page.text or "").strip()
            if not text:
                continue
            score = _compute_relevance(text, keywords) + _doc_type_boost(title, doc.ref.doc_type)
            if page.needs_ocr:
                score += 0.1
            scored_chunks.append((score, f"=== {title} (sayfa {page.page_index + 1}) ===\n{text}"))

    if not scored_chunks:
        return ""

    scored_chunks.sort(key=lambda item: item[0], reverse=True)
    parts: list[str] = []
    used = 0
    for _score, chunk in scored_chunks:
        if used + len(chunk) > max_chars:
            remaining = max_chars - used
            if remaining > 200:
                parts.append(chunk[:remaining] + "\n...[metin kısaltıldı]")
            break
        parts.append(chunk)
        used += len(chunk) + 2
    return "\n\n".join(parts).strip()


def build_evidence_package(
    extracted: list[ExtractedDocument],
    *,
    max_images: int | None = None,
    max_edge: int | None = None,
    max_text_chars: int | None = None,
    include_images: bool = True,
    huv_codes: list[str] | None = None,
    sut_codes: list[str] | None = None,
    icd_codes: list[str] | None = None,
    patient_name: str | None = None,
    procedure_names: list[str] | None = None,
    extra_keywords: list[str] | None = None,
) -> EvidencePackage:
    """Çıkarılmış belgelerden MedGemma kanıt paketi üretir."""

    max_images = max_images if max_images is not None else settings.VISION_MAX_IMAGES
    max_edge = max_edge if max_edge is not None else settings.VISION_MAX_EDGE_PX
    max_text_chars = (
        max_text_chars if max_text_chars is not None else settings.TEXT_EVIDENCE_MAX_CHARS
    )

    package = EvidencePackage()
    relevance_keywords = _build_relevance_keywords(
        huv_codes,
        sut_codes,
        icd_codes,
        patient_name,
        procedure_names,
        extra_keywords,
    )

    clinical_extracted: list[ExtractedDocument] = []
    skipped_titles: list[str] = []
    for doc in extracted:
        if not doc.ref.exists or doc.error:
            continue
        role = classify_evidence_role(doc.ref.doc_type)
        title = doc.ref.title or doc.ref.doc_type or doc.ref.path.name
        if role in _NON_CLINICAL_ROLES:
            skipped_titles.append(f"{title} ({role})")
            continue
        clinical_extracted.append(doc)
    if skipped_titles:
        package.notes.append(
            f"MedGemma'ya gönderilmeyen belgeler (klinik dışı): "
            f"{', '.join(skipped_titles)}"
        )

    scored_pages: list[_ScoredPage] = []
    global_page_idx = 0

    for doc_index, doc in enumerate(clinical_extracted):
        title = doc.ref.title or doc.ref.doc_type or doc.ref.path.name
        package.document_titles.append(title)
        type_boost = _doc_type_boost(title, doc.ref.doc_type)
        if include_images:
            for page_in_doc, page in enumerate(doc.pages):
                for visual_path in _page_visual_paths(page):
                    global_page_idx += 1
                    page_text = page.text or ""
                    relevance = _compute_relevance(page_text, relevance_keywords) + type_boost
                    if page.needs_ocr:
                        relevance += 0.15
                    if page.ocr_quality is not None and page.ocr_quality < settings.OCR_MIN_QUALITY:
                        relevance -= 0.2
                    scored_pages.append(
                        _ScoredPage(
                            page_number=global_page_idx,
                            doc_index=doc_index,
                            page_in_doc=page_in_doc,
                            doc_title=title,
                            image_path=visual_path,
                            needs_ocr=page.needs_ocr,
                            relevance_score=relevance,
                            text=page_text[:200],
                        )
                    )

    package.text_evidence = _build_text_evidence(
        clinical_extracted,
        relevance_keywords,
        max_chars=max_text_chars,
    )

    low_quality_docs: list[str] = []
    for doc in clinical_extracted:
        if not doc.ref.exists or doc.error or not doc.combined_text:
            continue
        title = doc.ref.title or doc.ref.doc_type or doc.ref.path.name
        garbled_pages = sum(1 for p in doc.pages if p.text and _is_garbled_text(p.text))
        if garbled_pages > 0:
            low_quality_docs.append(title)
    if low_quality_docs:
        package.notes.append(
            f"Metin kalitesi düşük belge(ler): {', '.join(low_quality_docs)}. "
            "Gömülü metin bozuk olabilir; görsellere öncelik verin."
        )

    if include_images and scored_pages:
        selected, excluded = _select_pages(scored_pages, max_images)
        package.image_paths = [_resize_image(p.image_path, max_edge) for p in selected]
        package.selected_page_numbers = [p.page_number for p in selected]
        package.excluded_page_numbers = excluded

        total = len(scored_pages)
        if max_images > 0 and total > max_images:
            package.partial_vision = True
            page_nums = ", ".join(str(p.page_number) for p in selected)
            note = (
                f"{total} sayfadan en ilgili {max_images} tanesi MedGemma'ya gönderildi "
                f"(sayfa: {page_nums})."
            )
            if excluded:
                omitted = ", ".join(str(n) for n in excluded[:12])
                suffix = "…" if len(excluded) > 12 else ""
                note += f" Gönderilmeyen: {omitted}{suffix}."
            package.notes.append(note)

    return package

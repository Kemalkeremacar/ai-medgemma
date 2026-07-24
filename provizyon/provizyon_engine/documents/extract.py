"""Belge metin çıkarma + sayfa görseli render (karar sırası.txt Adım 3).

PyMuPDF (fitz) ile:
- PDF'ten gömülü metni çıkarır (varsa).
- Gömülü metin bozuksa (garbled font encoding) OCR'a yönlendirir.
- Taranmış sayfalarda gömülü görseli native çözünürlükte çıkarır.
- Metin yetersizse sayfayı görsele render eder; OCR ve MedGemma vision'a beslenir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import settings
from .source import DocumentRef

_WORD_PATTERN = re.compile(r"[a-zA-ZçğıöşüÇĞİÖŞÜ]{3,}")
_MIXED_CASE_PATTERN = re.compile(r"[a-zçğıöşü][A-ZÇĞİÖŞÜ]")
_NON_ALPHA_RATIO = re.compile(r"[^a-zA-ZçğıöşüÇĞİÖŞÜ0-9\s.,:;()/\-]")


def _is_garbled_text(text: str) -> bool:
    """Gömülü metnin bozuk (garbled font encoding / OCR kalıntısı) olup olmadığını tespit eder."""
    if len(text) < 40:
        return False
    tokens = [t for t in re.split(r"\s+", text.strip()) if t]
    if len(tokens) >= 15:
        token_avg = sum(len(t) for t in tokens) / len(tokens)
        if token_avg < 2.5:
            return True
    words = _WORD_PATTERN.findall(text)
    if len(words) < 5:
        return False
    mixed = sum(1 for w in words if _MIXED_CASE_PATTERN.search(w))
    if mixed / len(words) > 0.25:
        return True
    # Yüksek özel karakter oranı (bozuk encoding belirtisi).
    non_alpha = len(_NON_ALPHA_RATIO.findall(text))
    if non_alpha / len(text) > 0.15:
        return True
    avg_len = sum(len(w) for w in words) / len(words)
    if avg_len < 2.2 and len(words) >= 15:
        return True
    return False


def _page_needs_ocr(text: str) -> bool:
    if len(text.strip()) < settings.OCR_MIN_TEXT_CHARS:
        return True
    return _is_garbled_text(text)


@dataclass
class PageContent:
    """Tek bir belge sayfası: gömülü metin + sayfa render + gömülü görseller."""

    page_index: int
    text: str = ""
    image_path: Path | None = None
    embedded_image_paths: list[Path] = field(default_factory=list)
    ocr_source_path: Path | None = None
    needs_ocr: bool = False
    text_source: str = "embedded"  # embedded | ocr | empty
    ocr_quality: float | None = None  # 0.0–1.0; OCR sonrası metin kalitesi
    ocr_psm: int | None = None  # Seçilen Tesseract PSM modu


@dataclass
class ExtractedDocument:
    """Bir belgenin çıkarılmış içeriği."""

    ref: DocumentRef
    pages: list[PageContent] = field(default_factory=list)
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def combined_text(self) -> str:
        return "\n".join(p.text for p in self.pages if p.text).strip()

    @property
    def needs_ocr(self) -> bool:
        return any(p.needs_ocr for p in self.pages)


def _work_dir_for(ref: DocumentRef) -> Path:
    settings.WORK_DIR.mkdir(parents=True, exist_ok=True)
    sub = settings.WORK_DIR / ref.path.stem
    sub.mkdir(parents=True, exist_ok=True)
    return sub


def _save_pixmap(pix: Any, path: Path) -> None:
    import fitz  # noqa: PLC0415

    if pix.n > 4:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    pix.save(str(path))


def _extract_embedded_images(
    pdf: Any, page: Any, work_dir: Path, page_index: int
) -> list[Path]:
    """Sayfadaki tüm anlamlı gömülü görselleri native çözünürlükte çıkarır."""
    min_area = settings.EMBEDDED_IMAGE_MIN_AREA
    out_paths: list[Path] = []
    seen_xrefs: set[int] = set()
    img_idx = 0

    for img_info in page.get_images(full=True):
        xref = img_info[0]
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)
        try:
            pix = pdf.extract_image(xref)
            w, h = pix["width"], pix["height"]
            if w * h < min_area:
                continue
            out = work_dir / f"embedded_{page_index:04d}_{img_idx:03d}.png"
            out.write_bytes(pix["image"])
            out_paths.append(out)
            img_idx += 1
        except Exception:
            try:
                import fitz  # noqa: PLC0415

                pix = fitz.Pixmap(pdf, xref)
                if pix.width * pix.height < min_area:
                    continue
                out = work_dir / f"embedded_{page_index:04d}_{img_idx:03d}.png"
                _save_pixmap(pix, out)
                out_paths.append(out)
                img_idx += 1
            except Exception:
                continue
    return out_paths


def extract_document(ref: DocumentRef, *, render_images: bool = True) -> ExtractedDocument:
    """Belge tipine göre metin çıkarır ve gerekirse görsele render eder."""

    if not ref.exists:
        return ExtractedDocument(ref=ref, error=ref.error or "Belge mevcut değil.")

    if ref.kind == "pdf":
        return _extract_pdf(ref, render_images=render_images)
    if ref.kind == "image":
        return _extract_image(ref)
    if ref.kind == "text":
        return _extract_text(ref)
    return ExtractedDocument(ref=ref, error=f"Desteklenmeyen belge tipi: {ref.kind}")


def _extract_pdf(ref: DocumentRef, *, render_images: bool) -> ExtractedDocument:
    try:
        import fitz  # PyMuPDF  # noqa: PLC0415
    except ImportError:
        return ExtractedDocument(ref=ref, error="PyMuPDF (fitz) kurulu değil; PDF işlenemedi.")

    doc_out = ExtractedDocument(ref=ref)
    work_dir = _work_dir_for(ref) if render_images else None
    vision_zoom = settings.PDF_RENDER_DPI / 72.0
    try:
        with fitz.open(str(ref.path)) as pdf:
            doc_out.meta["page_count"] = pdf.page_count
            embedded_count = 0
            for index in range(pdf.page_count):
                page = pdf.load_page(index)
                text = (page.get_text() or "").strip()
                needs_ocr = _page_needs_ocr(text)
                if _is_garbled_text(text):
                    text = ""

                image_path: Path | None = None
                embedded_paths: list[Path] = []
                ocr_source: Path | None = None

                if work_dir is not None:
                    embedded_paths = _extract_embedded_images(pdf, page, work_dir, index)
                    embedded_count += len(embedded_paths)
                    if render_images:
                        matrix = fitz.Matrix(vision_zoom, vision_zoom)
                        pixmap = page.get_pixmap(matrix=matrix)
                        image_path = work_dir / f"page_{index:04d}.png"
                        pixmap.save(str(image_path))
                    if needs_ocr:
                        if embedded_paths:
                            ocr_source = max(
                                embedded_paths,
                                key=lambda p: p.stat().st_size if p.exists() else 0,
                            )
                        elif image_path is not None:
                            ocr_source = image_path

                doc_out.pages.append(
                    PageContent(
                        page_index=index,
                        text=text,
                        image_path=image_path,
                        embedded_image_paths=embedded_paths,
                        ocr_source_path=ocr_source,
                        needs_ocr=needs_ocr,
                        text_source="embedded" if text else "empty",
                    )
                )
            doc_out.meta["embedded_images"] = embedded_count
    except Exception as exc:
        doc_out.error = f"PDF okunamadı: {exc}"
    return doc_out


def _extract_image(ref: DocumentRef) -> ExtractedDocument:
    return ExtractedDocument(
        ref=ref,
        pages=[
            PageContent(
                page_index=0,
                text="",
                image_path=ref.path,
                ocr_source_path=ref.path,
                needs_ocr=True,
                text_source="empty",
            )
        ],
    )


def _extract_text(ref: DocumentRef) -> ExtractedDocument:
    try:
        text = ref.path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception as exc:
        return ExtractedDocument(ref=ref, error=f"Metin dosyası okunamadı: {exc}")
    return ExtractedDocument(
        ref=ref,
        pages=[PageContent(page_index=0, text=text, needs_ocr=False, text_source="embedded")],
    )

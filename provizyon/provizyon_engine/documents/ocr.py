"""Tesseract tabanlı OCR (karar sırası.txt Adım 3 - OCR gerekiyorsa).

Taranmış/görsel sayfalardan Türkçe (+İngilizce) metin çıkarır.
Gömülü PDF görselleri native çözünürlükte, sayfa render'ları yüksek DPI ile işlenir.
Ön işleme: gri ton, kontrast, gürültü azaltma (median), eğrilik düzeltme (deskew),
keskinleştirme, küçük görsellerde upscale, opsiyonel Otsu ikilileştirme.
Düşük kalite tespit edilirse alternatif PSM modları denenir.
Sonuçlar dosya bazında önbelleğe alınır (mtime ile invalidasyon).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .. import settings
from .extract import ExtractedDocument, PageContent, _is_garbled_text
from .ocr_cache import load_cached_ocr, save_cached_ocr

_WORD_RE = re.compile(r"[a-zA-ZçğıöşüÇĞİÖŞÜ]{2,}")
_VOWEL_RE = re.compile(r"[aeıioöuüAEIİOÖUÜ]")
_TURKISH_HINT = re.compile(
    r"[çğıöşüÇĞİÖŞÜ]|hasta|rapor|tarih|kimlik|epikriz|tanı|tani|dr\.|hekim",
    re.IGNORECASE,
)

# PSM fallback sırası: varsayılan + alternatifler
_PSM_FALLBACK = (3, 4, 11)


@dataclass
class OCRResult:
    text: str = ""
    ran: bool = False
    error: str | None = None
    quality: float = 0.0
    psm: int | None = None


_TESSERACT_READY: bool | None = None


def tesseract_available() -> bool:
    global _TESSERACT_READY
    if _TESSERACT_READY is not None:
        return _TESSERACT_READY
    try:
        import pytesseract  # noqa: PLC0415

        pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD
        pytesseract.get_tesseract_version()
        _TESSERACT_READY = True
    except Exception:
        _TESSERACT_READY = False
    return _TESSERACT_READY


def ocr_quality_score(text: str) -> float:
    """OCR çıktısının okunabilirlik skoru (0.0–1.0)."""

    cleaned = (text or "").strip()
    if len(cleaned) < 10:
        return 0.0
    if _is_garbled_text(cleaned):
        return 0.15

    words = _WORD_RE.findall(cleaned)
    if len(words) < 3:
        return 0.2

    score = 0.35
    avg_word = sum(len(w) for w in words) / len(words)
    if avg_word >= 3.0:
        score += 0.15
    if avg_word >= 4.5:
        score += 0.1
    if _TURKISH_HINT.search(cleaned):
        score += 0.2
    if len(cleaned) >= 80:
        score += 0.1
    if len(cleaned) >= 300:
        score += 0.1

    # Garbled tespitini geçen ama anlamsız (kısa parça yığını / sesli harfsiz)
    # OCR çöpünü cezalandır: gerçek dilde kelimeler sesli harf içerir ve
    # metin ağırlıklı olarak 1-2 karakterlik parçalardan oluşmaz.
    tokens = [t for t in cleaned.split() if t]
    if len(tokens) >= 6:
        short_ratio = sum(1 for t in tokens if len(t) <= 2) / len(tokens)
        if short_ratio > 0.35:
            score -= 0.3
    vowel_words = sum(1 for w in words if _VOWEL_RE.search(w))
    if vowel_words / len(words) < 0.6:
        score -= 0.2

    return max(0.0, min(score, 1.0))


def _estimate_skew_angle(gray: "Image.Image") -> float:
    """Projeksiyon profili varyansını maksimize eden eğrilik açısını (derece) bulur.

    numpy yoksa 0.0 döner (deskew atlanır). Metin satırları yataya hizalandığında
    satır-toplamı varyansı en yüksek olur.
    """
    try:
        import numpy as np  # noqa: PLC0415
    except Exception:
        return 0.0

    # Hız için küçült ve ikilileştir (koyu metin = 1). Eğrilik açısı düşük
    # çözünürlükte de güvenilir tahmin edilir; 600px iyi bir denge.
    small = gray.copy()
    max_edge = 600
    w, h = small.size
    if max(w, h) > max_edge:
        scale = max_edge / max(w, h)
        small = small.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    arr = np.asarray(small, dtype=np.float32)
    binary = (arr < arr.mean()).astype(np.float32)
    if binary.sum() < 50:  # neredeyse boş sayfa
        return 0.0

    from PIL import Image  # noqa: PLC0415

    bin_img = Image.fromarray((binary * 255).astype("uint8"))
    max_angle = settings.OCR_DESKEW_MAX_ANGLE
    step = max(0.1, settings.OCR_DESKEW_STEP)
    best_angle = 0.0
    best_score = -1.0
    angle = -max_angle
    while angle <= max_angle + 1e-9:
        rotated = bin_img.rotate(angle, resample=Image.BILINEAR, fillcolor=0)
        row_sums = np.asarray(rotated, dtype=np.float32).sum(axis=1)
        score = float(np.var(row_sums))
        if score > best_score:
            best_score = score
            best_angle = angle
        angle += step
    return best_angle


def _preprocess_image(img: "Image.Image") -> "Image.Image":
    from PIL import Image, ImageFilter, ImageOps  # noqa: PLC0415

    gray = img.convert("L")
    gray = ImageOps.autocontrast(gray)

    if settings.OCR_DENOISE:
        gray = gray.filter(ImageFilter.MedianFilter(size=3))

    if settings.OCR_DESKEW:
        angle = _estimate_skew_angle(gray)
        if abs(angle) >= 0.5:
            gray = gray.rotate(
                angle, resample=Image.BICUBIC, expand=True, fillcolor=255
            )

    gray = gray.filter(ImageFilter.SHARPEN)

    w, h = gray.size
    min_edge = settings.OCR_MIN_EDGE_PX
    if max(w, h) < min_edge:
        scale = min_edge / max(w, h)
        gray = gray.resize((int(w * scale), int(h * scale)), resample=3)  # LANCZOS=3

    if settings.OCR_BINARIZE:
        gray = _otsu_binarize(gray)

    return gray


def _otsu_binarize(gray: "Image.Image") -> "Image.Image":
    """Global Otsu eşiği ile ikilileştirme (numpy yoksa görseli değiştirmez)."""
    try:
        import numpy as np  # noqa: PLC0415
    except Exception:
        return gray
    arr = np.asarray(gray, dtype=np.uint8)
    hist = np.bincount(arr.ravel(), minlength=256).astype(np.float64)
    total = arr.size
    sum_total = np.dot(np.arange(256), hist)
    sum_b = 0.0
    w_b = 0.0
    max_var = -1.0
    threshold = 127
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_total - sum_b) / w_f
        var_between = w_b * w_f * (m_b - m_f) ** 2
        if var_between > max_var:
            max_var = var_between
            threshold = t
    return gray.point(lambda p: 255 if p > threshold else 0, mode="L")


def _render_page_high_dpi(source_pdf: Path, page_index: int) -> Path | None:
    """OCR için yüksek DPI sayfa render'ı (gömülü görsel yoksa)."""
    try:
        import fitz  # noqa: PLC0415

        work = settings.WORK_DIR / "ocr_render" / source_pdf.stem
        work.mkdir(parents=True, exist_ok=True)
        out = work / f"ocr_page_{page_index:04d}.png"
        if out.exists():
            return out
        zoom = settings.OCR_DPI / 72.0
        with fitz.open(str(source_pdf)) as pdf:
            page = pdf.load_page(page_index)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            pix.save(str(out))
        return out
    except Exception:
        return None


def _tesseract_string(img: "Image.Image", psm: int) -> str:
    import pytesseract  # noqa: PLC0415

    pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD
    config = f"--oem 3 --psm {psm}"
    return pytesseract.image_to_string(img, lang=settings.OCR_LANG, config=config)


def _prepared_image(img: "Image.Image", preprocess: bool) -> "Image.Image":
    return _preprocess_image(img) if preprocess else img


def ocr_image(
    image_path: Path,
    *,
    preprocess: bool | None = None,
    psm: int | None = None,
) -> OCRResult:
    if not tesseract_available():
        return OCRResult(ran=False, error="Tesseract/pytesseract kullanılamıyor.")
    psm = psm if psm is not None else settings.OCR_PSM
    do_pp = preprocess if preprocess is not None else settings.OCR_PREPROCESS
    try:
        from PIL import Image  # noqa: PLC0415

        with Image.open(str(image_path)) as img:
            text = _tesseract_string(_prepared_image(img, do_pp), psm)
        cleaned = (text or "").strip()
        return OCRResult(text=cleaned, ran=True, quality=ocr_quality_score(cleaned), psm=psm)
    except Exception as exc:
        return OCRResult(ran=False, error=f"OCR hatası: {exc}")


def ocr_image_best_psm(image_path: Path) -> OCRResult:
    """Birden fazla PSM dener; en yüksek kalite skorlu sonucu döner.

    Ön işleme (deskew/denoise dahil) görsel başına yalnızca bir kez uygulanır;
    tüm PSM denemeleri aynı hazırlanmış görsel üzerinde çalışır.
    """
    if not tesseract_available():
        return OCRResult(ran=False, error="Tesseract/pytesseract kullanılamıyor.")

    # PSM sırası, tekrarları koruyarak tekilleştir.
    psms: list[int] = []
    for psm in (settings.OCR_PSM, *_PSM_FALLBACK):
        if psm not in psms:
            psms.append(psm)

    candidates: list[OCRResult] = []
    try:
        from PIL import Image  # noqa: PLC0415

        with Image.open(str(image_path)) as img:
            prepared = _prepared_image(img, settings.OCR_PREPROCESS)
            for psm in psms:
                text = (_tesseract_string(prepared, psm) or "").strip()
                if text:
                    candidates.append(
                        OCRResult(text=text, ran=True, quality=ocr_quality_score(text), psm=psm)
                    )
    except Exception as exc:
        return OCRResult(ran=False, error=f"OCR hatası: {exc}")

    if not candidates:
        return OCRResult(ran=True, text="", quality=0.0, psm=settings.OCR_PSM)

    best = max(candidates, key=lambda r: (r.quality, len(r.text)))
    if best.quality < settings.OCR_MIN_QUALITY and len(candidates) > 1:
        # En iyi yine de düşükse en uzun metni tercih et (kısmi okuma).
        alt = max(candidates, key=lambda r: len(r.text))
        if alt.quality >= best.quality * 0.85:
            return alt
    return best


def _page_has_visuals(page: PageContent) -> bool:
    return page.image_path is not None or bool(page.embedded_image_paths)


def _should_ocr_page(page: PageContent) -> bool:
    if page.needs_ocr:
        return True
    if not settings.OCR_ALL_PAGES or not _page_has_visuals(page):
        return False
    # Gömülü metin yeterli ve temizse gereksiz OCR'dan kaçın.
    text = (page.text or "").strip()
    if text and not _is_garbled_text(text) and len(text) >= settings.OCR_MIN_TEXT_CHARS:
        return False
    return True


def _merge_page_text(
    page: PageContent,
    primary: str,
    extras: list[str],
    *,
    quality: float | None = None,
    psm: int | None = None,
) -> None:
    parts = [primary.strip()] if primary.strip() else []
    for chunk in extras:
        chunk = chunk.strip()
        if chunk and chunk not in parts:
            parts.append(chunk)
    page.text = "\n".join(parts).strip()
    if page.text:
        page.text_source = "ocr" if page.needs_ocr or not primary.strip() else page.text_source
        page.needs_ocr = False
        page.ocr_quality = quality if quality is not None else ocr_quality_score(page.text)
        if psm is not None:
            page.ocr_psm = psm


def _ocr_embedded_images(page: PageContent, extracted: ExtractedDocument) -> list[str]:
    texts: list[str] = []
    for emb_path in page.embedded_image_paths:
        if not emb_path.exists():
            continue
        result = ocr_image_best_psm(emb_path)
        if result.ran and result.text:
            texts.append(result.text)
        elif result.error:
            extracted.meta.setdefault("ocr_errors", []).append(
                {"page": page.page_index, "embedded": emb_path.name, "error": result.error}
            )
    return texts


def ocr_document(extracted: ExtractedDocument) -> ExtractedDocument:
    if not any(_should_ocr_page(p) for p in extracted.pages):
        return extracted

    source = extracted.ref.path
    cached = load_cached_ocr(source)
    to_cache: dict[int, str] = dict(cached) if cached else {}
    new_pages_ocred = False

    for page in extracted.pages:
        if not _should_ocr_page(page):
            if page.text.strip():
                page.ocr_quality = ocr_quality_score(page.text)
            continue

        # Önbellek isabeti: aynı dosya (mtime aynı) daha önce OCR'landıysa
        # Tesseract'ı tekrar çağırmadan kaydedilmiş metni uygula.
        if cached is not None and page.page_index in cached:
            cached_text = cached[page.page_index].strip()
            if cached_text:
                page.text = cached_text
                page.text_source = "ocr"
                page.needs_ocr = False
                page.ocr_quality = ocr_quality_score(cached_text)
                continue

        ocr_path = page.ocr_source_path
        if ocr_path is None and source.suffix.lower() == ".pdf":
            ocr_path = _render_page_high_dpi(source, page.page_index)
        if ocr_path is None:
            ocr_path = page.image_path
        if ocr_path is None and page.embedded_image_paths:
            ocr_path = page.embedded_image_paths[0]

        primary = page.text
        if primary.strip() and _is_garbled_text(primary):
            page.needs_ocr = True
            primary = ""

        supplemental: list[str] = []
        best_psm: int | None = None
        best_quality: float | None = None
        if ocr_path is not None:
            result = ocr_image_best_psm(ocr_path)
            if result.ran and result.text:
                best_psm = result.psm
                best_quality = result.quality
                if page.needs_ocr or not primary.strip():
                    primary = result.text
                else:
                    supplemental.append(result.text)
            elif result.error:
                extracted.meta.setdefault("ocr_errors", []).append(
                    {"page": page.page_index, "error": result.error}
                )

        extras = supplemental + _ocr_embedded_images(page, extracted)
        if primary.strip() or extras:
            merged_quality = best_quality if best_quality is not None else ocr_quality_score(primary)
            _merge_page_text(page, primary, extras, quality=merged_quality, psm=best_psm)

        if page.text_source == "ocr" and page.text.strip():
            to_cache[page.page_index] = page.text
            new_pages_ocred = True

    if new_pages_ocred:
        save_cached_ocr(source, to_cache)

    return extracted

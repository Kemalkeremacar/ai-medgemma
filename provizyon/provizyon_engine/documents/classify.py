"""Belge türü ve cinsiyet tahmini (dosya adı + metin + işlem bağlamı + hafif OCR).

Intake (hızlı peek/OCR) ve pipeline (tam metin/OCR sonrası) aynı kuralları kullanır.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .. import settings
from ..models import Cinsiyet
from .extract import ExtractedDocument
from .source import IMAGE_SUFFIXES

_TR_MAP = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")

# (anahtar kelimeler, belge_türü, güven) — daha spesifik kurallar önce.
_DOC_TYPE_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("epikriz", "taburcu"), "epikriz", "high"),
    (
        (
            "nufus cuzdani", "nufus cuzdan", "kimlik karti", "kimlik fotokopi",
            "kimlik belgesi", "tc kimlik kart", "t.c. kimlik kart",
            "turkiye cumhuriyeti kimlik", "identity card", "republic of turkey",
        ),
        "kimlik",
        "high",
    ),
    (("hizmet dokum", "hizmet döküm", "provizyon detay", "brans provizyon", "uye sicil no", "hasta ad soyad"), "rapor", "high"),
    (("odyogram formu", "odyometri", "saf ses", "timponometri", "impedans audiometri"), "rapor", "high"),
    (("oct", "optik koherans", "goz tomografi"), "rapor", "high"),
    (("radyoloji rapor", "radyoloji", "rad report"), "radyoloji_raporu", "high"),
    (("bt rapor", "tomografi rapor", "mr rapor", "manyetik rezonans"), "radyoloji_raporu", "high"),
    (("tomografi", "tomografi̇", "bt ", " bilgisayarli tomografi", "paranazal"), "radyoloji_raporu", "medium"),
    (("anjiyo", "anjiyografi", "koroner", "kakt"), "radyoloji_raporu", "medium"),
    (("ultrason", "doppler", "ekokardiyografi"), "radyoloji_raporu", "medium"),
    (("elektrokardiyogram", "ekg rapor"), "ekg", "high"),
    (("ekg",), "ekg", "medium"),
    (("stres test", "treadmill", "efor test", "kardiyovaskuler stres"), "stres_testi", "high"),
    (("e-fatura", "efatura", "mali hizmetler", "fatura"), "fatura", "high"),
    (("katilim payi", "katilim"), "fatura", "medium"),
    (("hasta bilgi formu", "hastabilgi", "hasta_bilgi"), "hasta_bilgi_formu", "high"),
    (("muayene formu",), "muayene_formu", "high"),
    (("ibraname", "ibra"), "ibraname", "high"),
    (("order rapor", "order"), "order_raporu", "high"),
    (("laboratuvar", "lab sonuc", "tahlil", "patoloji", "tetkik sonuc"), "rapor", "medium"),
    (("konsultasyon", "konsültasyon"), "rapor", "medium"),
    (("islem detay", "tetkik"), "rapor", "medium"),
    (("rapor",), "rapor", "low"),
)

# Popup işlem adından beklenen belge türü ipucu.
_PROCEDURE_DOC_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("bilgisayarli toraks", "toraks tomografi", "bt toraks", "tomografi", "paranazal", "sinus"), "radyoloji_raporu"),
    (("anjiyo", "koroner", "kakt", "bt anjiyo"), "radyoloji_raporu"),
    (("ekokardiyografi", "doppler", "m mode", "b mode", "ultrason"), "radyoloji_raporu"),
    (("ekg", "elektrokardiyogram"), "ekg"),
    (("stres test", "efor test", "treadmill", "kardiyovaskuler"), "stres_testi"),
    (("odyometri", "odyogram", "saf ses", "timponometri", "impedans", "isitme"), "rapor"),
    (("oct", "optik koherans", "goz tomografi"), "rapor"),
    (("muayene", "konsultasyon"), "muayene_formu"),
)

_GENDER_RE = re.compile(
    r"cinsiyet(?:i)?\s*:?\s*(erkek|e\b|kad[ıi]n|k\b|bay|bayan)",
    re.IGNORECASE,
)
_AGE_GENDER_RE = re.compile(
    r"(?:\d{1,3}\s*/\s*(erkek|kad[ıi]n|bay|bayan)"
    r"|(erkek|kad[ıi]n|bay|bayan)\s*/\s*\d{1,3})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DocTypeGuess:
    doc_type: str
    confidence: str = "medium"  # high | medium | low
    source: str = "text"  # filename | peek | ocr_peek | full_text | procedure_hint | code_fallback | garbled_fallback | ocr_fallback


def _fold(text: str) -> str:
    return text.translate(_TR_MAP).lower()


def _match_rules(text: str, *, max_chars: int = 2000) -> DocTypeGuess | None:
    if not text or len(text) < 15:
        return None
    folded = _fold(text[:max_chars])
    for keys, doc_type, confidence in _DOC_TYPE_RULES:
        if any(_fold(k) in folded for k in keys):
            return DocTypeGuess(doc_type=doc_type, confidence=confidence, source="text")
    return None


def _match_procedure_hints(
    text: str,
    procedure_names: list[str] | None,
    *,
    max_chars: int = 2000,
) -> DocTypeGuess | None:
    if not text or not procedure_names:
        return None
    folded_text = _fold(text[:max_chars])
    proc_folded = _fold(" ".join(procedure_names))
    for keys, doc_type in _PROCEDURE_DOC_HINTS:
        proc_hit = any(_fold(k) in proc_folded for k in keys)
        text_hit = any(_fold(k) in folded_text for k in keys)
        if proc_hit and text_hit:
            return DocTypeGuess(doc_type=doc_type, confidence="medium", source="procedure_hint")
    return None


def _looks_like_kimlik_card(text: str) -> bool:
    """Gerçek kimlik kartı; provizyon formu ve klinik raporları ayır."""
    folded = _fold(text[:2000])
    if any(k in folded for k in ("uye sicil", "hasta ad soyad", "provizyon no", "provizyon detay")):
        return False
    if any(
        k in folded
        for k in ("epikriz", "ekokardiyografi", "radyoloji rapor", "sonuc raporu", "patoloji", "odyometri")
    ):
        return False
    if re.search(r"kimlik\s*kart", folded):
        return True
    if "turkiye cumhuriyeti" in folded and "kimlik" in folded:
        return True
    if re.search(r"tc\s*kimlik\s*kart", folded):
        return True
    return False


def classify_document(
    filename: str,
    text: str | None = None,
    *,
    procedure_names: list[str] | None = None,
    max_chars: int = 2000,
    source: str = "text",
) -> DocTypeGuess | None:
    """Dosya adı + metin + işlem ipucu ile belge türü tahmini."""

    name_guess = _match_rules(_fold(filename), max_chars=len(filename) + 50)
    if "kimlik" in _fold(filename):
        return DocTypeGuess(doc_type="kimlik", confidence="high", source="filename")
    if name_guess:
        name_guess = DocTypeGuess(
            doc_type=name_guess.doc_type,
            confidence=name_guess.confidence,
            source="filename",
        )
        if name_guess.confidence == "high":
            return name_guess

    if text:
        if _looks_like_kimlik_card(text):
            return DocTypeGuess(doc_type="kimlik", confidence="high", source=source)
        text_guess = _match_rules(text, max_chars=max_chars)
        if text_guess:
            return DocTypeGuess(
                doc_type=text_guess.doc_type,
                confidence=text_guess.confidence,
                source=source,
            )
        hint = _match_procedure_hints(text, procedure_names, max_chars=max_chars)
        if hint:
            return hint

    return name_guess


def guess_doc_type_from_name(name: str) -> str | None:
    guess = classify_document(name, None)
    return guess.doc_type if guess else None


def guess_doc_type_from_text(
    text: str,
    *,
    max_chars: int = 2000,
    procedure_names: list[str] | None = None,
) -> str | None:
    guess = classify_document("", text, procedure_names=procedure_names, max_chars=max_chars)
    return guess.doc_type if guess else None


def _apply_guess(doc: ExtractedDocument, guess: DocTypeGuess) -> None:
    doc.ref.doc_type = guess.doc_type
    doc.ref.meta["doc_type_confidence"] = guess.confidence
    doc.ref.meta["doc_type_source"] = guess.source


def infer_gender_from_text(text: str) -> Cinsiyet | None:
    if not text:
        return None
    m = _GENDER_RE.search(text)
    if m:
        token = m.group(1).lower()
    else:
        m2 = _AGE_GENDER_RE.search(text)
        if not m2:
            return None
        token = (m2.group(1) or m2.group(2) or "").lower()
    if token in {"erkek", "e", "bay"}:
        return Cinsiyet.ERKEK
    if token in {"kadın", "kadin", "k", "bayan"}:
        return Cinsiyet.KADIN
    return None


def gender_from_hizmet_alan(hizmet_alan: str | None) -> Cinsiyet:
    if not hizmet_alan:
        return Cinsiyet.BILINMIYOR
    s = hizmet_alan.lower()
    if any(k in s for k in ("kız", "kiz", "anne", "eş (kadın)", "eş(kadın)", "kadin")):
        return Cinsiyet.KADIN
    if any(k in s for k in ("erkek", "baba", "oğul", "ogul")):
        return Cinsiyet.ERKEK
    return Cinsiyet.BILINMIYOR


def peek_pdf_text(path: Path, *, max_pages: int = 3) -> str:
    try:
        import fitz  # noqa: PLC0415

        parts: list[str] = []
        with fitz.open(str(path)) as pdf:
            for index in range(min(max_pages, pdf.page_count)):
                chunk = (pdf.load_page(index).get_text() or "").strip()
                if chunk:
                    parts.append(chunk)
        return "\n".join(parts)
    except Exception:
        return ""


def _peek_pdf_ocr(path: Path, *, max_pages: int = 1) -> str:
    """Taranmış PDF'in ilk sayfasında hafif OCR (intake sınıflandırması için)."""
    try:
        import fitz  # noqa: PLC0415

        from .ocr import ocr_image, tesseract_available

        if not tesseract_available():
            return ""
        parts: list[str] = []
        work = settings.WORK_DIR / "peek_ocr" / path.stem
        work.mkdir(parents=True, exist_ok=True)
        zoom = settings.PDF_RENDER_DPI / 72.0
        with fitz.open(str(path)) as pdf:
            for index in range(min(max_pages, pdf.page_count)):
                page = pdf.load_page(index)
                embedded = (page.get_text() or "").strip()
                if len(embedded) >= settings.OCR_MIN_TEXT_CHARS:
                    parts.append(embedded)
                    continue
                matrix = fitz.Matrix(zoom, zoom)
                pixmap = page.get_pixmap(matrix=matrix)
                img_path = work / f"peek_{index:04d}.png"
                pixmap.save(str(img_path))
                result = ocr_image(img_path)
                if result.text:
                    parts.append(result.text)
        return "\n".join(parts)
    except Exception:
        return ""


def peek_file_text(path: Path, *, max_pages: int = 3, ocr_if_empty: bool = True) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = peek_pdf_text(path, max_pages=max_pages)
        if ocr_if_empty and len(text.strip()) < 15:
            ocr_text = _peek_pdf_ocr(path, max_pages=1)
            if ocr_text.strip():
                return ocr_text
        return text
    if suffix in {".txt", ".md"}:
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:2000]
        except Exception:
            return ""
    if suffix in IMAGE_SUFFIXES and ocr_if_empty:
        from .ocr import ocr_image

        result = ocr_image(path)
        return (result.text or "")[:2000]
    return ""


def refine_doc_types(
    extracted: list[ExtractedDocument],
    *,
    procedure_names: list[str] | None = None,
) -> list[str]:
    """OCR/extract sonrası doc_type null olan belgeleri sınıflandırır.

    Yalnızca düşük güvenli tahminler uyarı üretir; yüksek/orta güven sessizce kaydedilir.
    """

    from .extract import _is_garbled_text

    notes: list[str] = []
    for doc in extracted:
        if doc.ref.doc_type:
            doc.ref.meta.setdefault("doc_type_confidence", "medium")
            doc.ref.meta.setdefault("doc_type_source", "peek")
            continue

        text = doc.combined_text or ""
        guess: DocTypeGuess | None = None
        if text:
            guess = classify_document(
                doc.ref.path.name,
                text,
                procedure_names=procedure_names,
                source="full_text",
            )

        if not guess:
            from .procedure_match import extract_codes_from_text

            if text:
                codes = extract_codes_from_text(text)
                if codes.huv or codes.sut:
                    guess = DocTypeGuess("rapor", confidence="low", source="code_fallback")
            if not guess and text and _is_garbled_text(text) and len(text) < 200:
                guess = DocTypeGuess("rapor", confidence="low", source="garbled_fallback")
            if not guess and doc.needs_ocr and len(text) < 50:
                guess = DocTypeGuess("rapor", confidence="low", source="ocr_fallback")

        if not guess:
            continue

        _apply_guess(doc, guess)
        if guess.confidence == "low":
            notes.append(
                f"Belge türü düşük güvenle tahmin edildi: {doc.ref.path.name} -> "
                f"{guess.doc_type} ({guess.source})"
            )
    return notes


_CLINICAL_EVIDENCE_TYPES = frozenset({
    "epikriz", "rapor", "radyoloji_raporu", "ekg", "stres_testi",
    "muayene_formu", "order_raporu",
})
_PATIENT_VERIFICATION_TYPES = frozenset({"kimlik"})
_INVOICE_TYPES = frozenset({"fatura", "ibraname"})

_EVIDENCE_ROLE_LABELS: dict[str, str] = {
    "klinik_kanit": "Klinik Kanıt Belgesi",
    "hasta_dogrulama": "Hasta Doğrulama Belgesi",
    "fatura_hizmet": "Fatura / Hizmet Belgesi",
    "ek_belge": "Ek Belge",
}


def classify_evidence_role(doc_type: str | None) -> str:
    """Belge türünden kanıt rolü çıkarır."""

    if not doc_type:
        return "ek_belge"
    if doc_type in _PATIENT_VERIFICATION_TYPES:
        return "hasta_dogrulama"
    if doc_type in _CLINICAL_EVIDENCE_TYPES:
        return "klinik_kanit"
    if doc_type in _INVOICE_TYPES:
        return "fatura_hizmet"
    return "ek_belge"


def evidence_role_label(role: str) -> str:
    return _EVIDENCE_ROLE_LABELS.get(role, role)


_DOC_TYPE_LABELS: dict[str, str] = {
    "epikriz": "Epikriz",
    "radyoloji_raporu": "Radyoloji Raporu",
    "kimlik": "Kimlik Belgesi",
    "fatura": "Fatura",
    "hasta_bilgi_formu": "Hasta Bilgi Formu",
    "muayene_formu": "Muayene Formu",
    "ekg": "EKG",
    "stres_testi": "Stres Testi",
    "ibraname": "İbraname",
    "order_raporu": "Order Raporu",
    "rapor": "Rapor",
}

_HEADING_KEYWORDS: tuple[str, ...] = (
    "epikriz", "rapor", "fatura", "kimlik", "hasta", "muayene", "hizmet",
    "radyoloji", "odyometri", "odyogram", "ekokardiyografi", "hastane", "hospital",
    "t.c.", "tc kimlik", "protokol", "tedavi", "sonuc", "sonuç", "bulgu",
    "ameliyat", "kardiyoloji", "kbb", "goz", "laboratuvar", "patoloji",
    "taburcu", "yatis", "yatış", "klinik", "poliklinik", "acil", "servis",
    "universite", "üniversite", "devlet", "ozel", "özel", "saglik", "sağlık",
    "tibbi", "tıbbi", "tetkik", "analiz", "tomografi", "ultrason", "mr ",
    "efor", "holter", "spirometri", "endoskopi", "biyopsi", "konsultasyon",
)

_DATE_PATTERN = re.compile(
    r"(\d{1,2})[./\-](\d{1,2})[./\-](20\d{2})"
)

_FILENAME_DOC_HINTS: dict[str, str] = {
    "epikriz": "Epikriz",
    "rapor": "Rapor",
    "fatura": "Fatura",
    "lab": "Laboratuvar",
    "radyoloji": "Radyoloji",
    "ekg": "EKG",
    "tahlil": "Tahlil",
    "sonuc": "Sonuç",
}

_SKIP_HEADING_PATTERNS = (
    "popuppage", "saglik.tzhvakfi", "http://", "https://",
    "tarih olustur", "tarih oluştur",
)


def _line_heading_score(line: str) -> float:
    """Satırın belge başlığı olmaya uygunluğunu puanlar (-1 = elenir)."""
    from .extract import _is_garbled_text

    cleaned = " ".join(line.split())
    if len(cleaned) < 8:
        return -1.0
    folded = _fold(cleaned)
    if any(pat in folded for pat in _SKIP_HEADING_PATTERNS):
        return -1.0
    if cleaned.lstrip().startswith("|") or cleaned.count("|") >= 2:
        return -1.0
    if _is_garbled_text(cleaned):
        return -1.0

    letters = sum(1 for c in cleaned if c.isalpha())
    if letters < 6 or letters / max(len(cleaned), 1) < 0.45:
        return -1.0

    tokens = cleaned.split()
    if sum(1 for t in tokens if len(t) <= 1) > max(1, len(tokens) // 3):
        return -1.0
    # Çok fazla özel karakter.
    weird = sum(1 for c in cleaned if c in "|<>{}[]\\^~`")
    if weird > 2:
        return -1.0

    score = 0.0
    for kw in _HEADING_KEYWORDS:
        if kw in folded:
            score += 2.0
    score += min(len(cleaned) / 35.0, 2.0)
    if any(t[0].isupper() for t in tokens[:4] if t and t[0].isalpha()):
        score += 0.5
    return score


def _best_heading(text: str, *, max_len: int = 55) -> str | None:
    best_score = 0.0
    best_line: str | None = None
    for line in text.splitlines()[:40]:
        cleaned = " ".join(line.split())
        score = _line_heading_score(cleaned)
        if score > best_score:
            best_score = score
            best_line = cleaned[:max_len]
    if best_score < 1.5:
        return None
    return best_line


def _extract_doc_date(text: str) -> str | None:
    """Belge metninden en erken tarihi çıkarır (ör. '03.01.2025')."""
    for m in _DATE_PATTERN.finditer(text[:2000]):
        day, month, year = m.group(1), m.group(2), m.group(3)
        if 1 <= int(day) <= 31 and 1 <= int(month) <= 12:
            return f"{day.zfill(2)}.{month.zfill(2)}.{year}"
    return None


def _filename_label_hint(filename: str) -> str | None:
    """Dosya adından belge türü ipucu çıkarır (UUID/numerik isimler hariç)."""
    stem = Path(filename).stem.lower()
    if re.fullmatch(r"[\d_\-]+", stem) or len(stem) > 40:
        return None
    folded = _fold(stem)
    for key, label in _FILENAME_DOC_HINTS.items():
        if key in folded:
            return label
    return None


def enrich_document_titles(extracted: list[ExtractedDocument]) -> None:
    """UUID dosya adları yerine anlamlı belge başlıkları üretir.

    Başlık formatı: "{Tür} ({N} sayfa) - {tarih}: {heading}"
    """
    for doc in extracted:
        if doc.ref.title:
            continue
        dtype = doc.ref.doc_type
        label = _DOC_TYPE_LABELS.get(dtype or "", dtype or "Belge")

        if not label or label == "Belge":
            fn_hint = _filename_label_hint(doc.ref.path.name if doc.ref.path else "")
            if fn_hint:
                label = fn_hint

        heading = _best_heading(doc.combined_text or "")
        doc_date = _extract_doc_date(doc.combined_text or "")
        pages = len(doc.pages)
        page_hint = f" ({pages} sayfa)" if pages > 1 else ""
        date_hint = f" - {doc_date}" if doc_date else ""

        if heading:
            doc.ref.title = f"{label}{page_hint}{date_hint}: {heading}"
        else:
            doc.ref.title = f"{label}{page_hint}{date_hint}"


def infer_gender_from_documents(extracted: list[ExtractedDocument]) -> Cinsiyet | None:
    for doc in extracted:
        inferred = infer_gender_from_text(doc.combined_text)
        if inferred is not None:
            return inferred
    return None

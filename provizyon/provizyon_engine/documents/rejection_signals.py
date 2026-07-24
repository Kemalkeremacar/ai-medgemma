"""Belgelerde önceki iade/red yazışması gibi deterministik uyarı işaretleri."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# OCR ve e-posta metinlerinde görülen iade/red kalıpları (case-insensitive).
_REJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:ödenmez|odenmez)\b",
        r"\biade\s+neden",
        r"\biade\s+edil",
        r"\biade\s+karar",
        r"\bred\s+tutar\b",
        r"\bfatura(?:nız)?\s+bu\s+doğrultuda\s+düzenle",
        r"\btalep\s+edilen\s+tetkik.*(?:ödenmez|odenmez|iade)",
        r"\bşikayet.*uyumsuz.*(?:ödenmez|odenmez)",
    )
)


def _normalize_text(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text)
    return folded.encode("ascii", "ignore").decode("ascii").lower()


def scan_text_for_rejection_signals(text: str, *, max_hits: int = 5) -> list[str]:
    """Metinde iade/red sinyali arar; kısa kanıt parçaları döner."""

    if not text or not text.strip():
        return []
    norm = _normalize_text(text)
    hits: list[str] = []
    seen: set[str] = set()
    for pattern in _REJECTION_PATTERNS:
        for match in pattern.finditer(norm):
            start = max(0, match.start() - 40)
            end = min(len(text), match.end() + 80)
            snippet = " ".join(text[start:end].split())
            key = snippet[:60].lower()
            if key in seen:
                continue
            seen.add(key)
            hits.append(snippet[:160])
            if len(hits) >= max_hits:
                return hits
    return hits


def scan_extracted_documents(extracted: list[Any]) -> list[str]:
    """OCR/çıkarım belgelerinde iade-red sinyallerini toplar."""

    hits: list[str] = []
    seen: set[str] = set()
    for doc in extracted or []:
        text = (
            getattr(doc, "combined_text", None)
            or getattr(doc, "full_text", None)
            or getattr(doc, "text", None)
            or ""
        )
        if not text and isinstance(doc, dict):
            text = doc.get("combined_text") or doc.get("full_text") or doc.get("text") or ""
        for snippet in scan_text_for_rejection_signals(str(text)):
            key = snippet[:60].lower()
            if key in seen:
                continue
            seen.add(key)
            hits.append(snippet)
    return hits[:8]

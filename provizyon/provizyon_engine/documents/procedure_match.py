"""Belge metninden HUV/SUT/ICD-10 kodu çıkarma."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUV_RE = re.compile(r"\b(\d{2}\.\d{3,6})\b")
_TZH_RE = re.compile(r"\b(TZH\.\S+)", re.IGNORECASE)
# SUT: 6 haneli, 1-9 ile başlar; 11 haneli TC ile karışmasın diye sınırlandırılmış.
_SUT_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_ICD_RE = re.compile(r"\b([A-Z]\d{2}(?:\.\d{1,2})?)\b")


@dataclass
class ExtractedCodes:
    huv: list[str] = field(default_factory=list)
    sut: list[str] = field(default_factory=list)
    tzh: list[str] = field(default_factory=list)
    icd: list[str] = field(default_factory=list)


def _dedup(codes: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for code in codes:
        key = code.strip().upper()
        if key and key not in seen:
            seen.add(key)
            out.append(code.strip())
    return out


def _parse_huv(code: str) -> tuple[str, str] | None:
    m = re.fullmatch(r"(\d{2})\.(\d{3,6})", code.strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def _looks_like_date_token(code: str) -> bool:
    """01.2026 gibi ay.yıl / tarih parçalarını HUV kodu sayma."""

    parsed = _parse_huv(code)
    if not parsed:
        return False
    _major, minor = parsed
    if len(minor) == 4 and minor.startswith("20"):
        return True
    if len(minor) == 4:
        try:
            year = int(minor)
        except ValueError:
            return False
        return 1950 <= year <= 2035
    return False


def _looks_like_price(code: str, text: str) -> bool:
    """XX.XXX gibi fiyat/tutar parçalarını HUV kodu olarak sayma."""

    parsed = _parse_huv(code)
    if not parsed:
        return False
    _major, minor = parsed
    if len(minor) != 3:
        return False
    idx = text.find(code)
    if idx >= 0:
        after = text[idx + len(code): idx + len(code) + 3]
        if after.startswith(",") or after.startswith("."):
            return True
    return False


def extract_codes_from_text(text: str) -> ExtractedCodes:
    if not text:
        return ExtractedCodes()

    raw_huv = [c for c in _dedup(_HUV_RE.findall(text)) if not _looks_like_date_token(c)]
    huv = [c for c in raw_huv if not _looks_like_price(c, text)]
    tzh = _dedup(_TZH_RE.findall(text))
    icd = _dedup(_ICD_RE.findall(text))

    sut: list[str] = []
    for match in _SUT_RE.finditer(text):
        code = match.group(1)
        if code.startswith("0"):
            continue
        sut.append(code)
    sut = _dedup(sut)

    return ExtractedCodes(huv=huv, sut=sut, tzh=tzh, icd=icd)

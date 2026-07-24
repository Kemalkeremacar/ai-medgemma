"""TZH "Hizmet Döküm Formu" (popup) PDF ayrıştırıcı.

Bu PDF provizyonun kaynağıdır: hasta kimliği, üst tanı (ICD-10) ve hizmet
kalemleri (HUV kodları) burada yapılandırılmış olarak bulunur. Metin tabanlı
(gömülü metinli) bir PDF olduğundan PyMuPDF ile metin çıkarılıp düzenli
ifadelerle ayrıştırılır.

Tipik düzen:
    Üye Sicil No: 0030024
    Provizyon No: 3208035
    Hasta Ad Soyad : ABDULLAH AKDEMİR        # ad iki satıra bölünebilir
    TC Kimlik No 53491683054                  # bazı alanlarda iki nokta yok
    Doğum Tarihi 25-04-1980
    ...
    Üst Tanı / ICD 10 Kod / Ad
    Diğer
    K22
    Özofagusun diğer hastalıkları
    ...
    AYAKTA TEDAVİ
    Sıra Hizmet Kod Ad ...
    1
    24.73601
    BİLGİSAYARLI
    TORAKS TOMOGRAFİSİ
    12.589,50
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# ICD-10 kodu: bir harf + iki rakam (+ opsiyonel .alt). 'U' kodlarını da kapsar.
ICD_RE = re.compile(r"^[A-Z]\d{2}(?:\.\d{1,2})?$")
# HUV/hizmet kodu: 12.34567 biçimi veya TZH.xxx.
HUV_NUMERIC_RE = re.compile(r"^\d{2}\.\d{3,6}$")
HUV_TZH_RE = re.compile(r"^TZH\.\S+$", re.IGNORECASE)
SUT_NUMERIC_RE = re.compile(r"^[1-9]\d{5}$")
# Tutar satırı: 12.589,50 / 4.046 gibi (binlik nokta, ondalık virgül).
AMOUNT_RE = re.compile(r"^\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?$")
INT_ONLY_RE = re.compile(r"^\d{1,3}$")


@dataclass
class PopupDiagnosis:
    code: str
    name: str = ""


@dataclass
class PopupProcedure:
    code: str
    name: str = ""


@dataclass
class PopupData:
    provizyon_no: str | None = None
    hasta_ad: str | None = None
    tc: str | None = None
    dogum_tarihi: str | None = None  # dd-mm-yyyy
    uye_sicil: str | None = None
    hizmet_zamani: str | None = None  # dd-mm-yyyy HH:MM
    hizmet_alan: str | None = None
    uye_statu: str | None = None
    kurum: str | None = None
    diagnoses: list[PopupDiagnosis] = field(default_factory=list)
    procedures: list[PopupProcedure] = field(default_factory=list)
    raw_text: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def icd_codes(self) -> list[str]:
        return _dedup([d.code for d in self.diagnoses])

    @property
    def huv_codes(self) -> list[str]:
        """Tanı motorlarının anladığı sayısal HUV kodları (TZH/SUT hariç)."""

        return _dedup([p.code for p in self.procedures if HUV_NUMERIC_RE.match(p.code)])

    @property
    def sut_codes(self) -> list[str]:
        """SGK SUT işlem kodları (6 haneli)."""

        return _dedup([p.code for p in self.procedures if SUT_NUMERIC_RE.match(p.code)])


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        k = it.strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _extract_text(path: Path) -> str:
    import fitz  # noqa: PLC0415

    doc = fitz.open(path)
    try:
        return "\n".join(doc.load_page(i).get_text() for i in range(doc.page_count))
    finally:
        doc.close()


def _field(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else None


# Bir satırın yeni bir başlık/etiket olup olmadığını anlamak için (ad çıkarımında durmak için).
_LABEL_PREFIXES = (
    "Provizyon Durum",
    "Provizyon No",
    "Provizyon/Hizmet",
    "TC Kimlik",
    "Doğum Tarihi",
    "Paket No",
    "Üye Statü",
    "Üye Sicil",
    "Hizmet Alan",
    "Tevkifat",
)


def _parse_hasta_ad(lines: list[str]) -> str | None:
    for i, line in enumerate(lines):
        if line.startswith("Hasta Ad Soyad"):
            parts: list[str] = []
            after = line.split(":", 1)[1].strip() if ":" in line else ""
            if after:
                parts.append(after)
            j = i + 1
            while j < len(lines):
                nxt = lines[j].strip()
                if not nxt or nxt.startswith(_LABEL_PREFIXES):
                    break
                parts.append(nxt)
                j += 1
            name = " ".join(p for p in parts if p).strip()
            return re.sub(r"\s+", " ", name) or None
    return None


def _slice_between(lines: list[str], start_keys: tuple[str, ...], end_keys: tuple[str, ...]) -> list[str]:
    start = None
    for i, line in enumerate(lines):
        if any(k in line for k in start_keys):
            start = i + 1
            break
    if start is None:
        return []
    end = len(lines)
    for j in range(start, len(lines)):
        if any(k in lines[j] for k in end_keys):
            end = j
            break
    return [l.strip() for l in lines[start:end]]


def _parse_diagnoses(lines: list[str]) -> list[PopupDiagnosis]:
    block = _slice_between(
        lines,
        start_keys=("ICD 10 Kod", "ICD-10", "Üst Tanı"),
        end_keys=("AYAKTA TEDAVİ", "YATARAK", "Sıra Hizmet", "Sıra", "Hizmet Kod"),
    )
    out: list[PopupDiagnosis] = []
    for idx, line in enumerate(block):
        if ICD_RE.match(line):
            name = ""
            # İsim genelde kod satırının hemen ardındadır.
            if idx + 1 < len(block):
                cand = block[idx + 1].strip()
                if cand and not ICD_RE.match(cand) and cand != "Diğer":
                    name = cand
            out.append(PopupDiagnosis(code=line, name=name))
    return out


def _parse_procedures(lines: list[str]) -> list[PopupProcedure]:
    # İşlem bölgesi: ilk "AYAKTA TEDAVİ"/"Sıra" başlığından "Toplam"/footer'a kadar.
    start = None
    for i, line in enumerate(lines):
        if "AYAKTA TEDAVİ" in line or "YATARAK TEDAVİ" in line:
            start = i + 1
            break
    if start is None:
        for i, line in enumerate(lines):
            if line.strip().startswith("Sıra") or "Hizmet Kod" in line:
                start = i + 1
                break
    if start is None:
        return []
    # Sütun başlığında "Toplam Fatura Tutarı" geçtiği için "Toplam" ile bölge
    # kapatılamaz. İşlemler genelde son bölümdür; sona kadar tarayıp kod
    # regex'iyle kalemleri toplarız (başlık/tutar/footer satırları eşleşmez).
    block = [l.strip() for l in lines[start:]]

    procs: list[PopupProcedure] = []
    k = 0
    while k < len(block):
        line = block[k]
        if HUV_NUMERIC_RE.match(line) or HUV_TZH_RE.match(line) or SUT_NUMERIC_RE.match(line):
            code = line
            name_parts: list[str] = []
            m = k + 1
            while m < len(block):
                nxt = block[m].strip()
                if (
                    not nxt
                    or AMOUNT_RE.match(nxt)
                    or HUV_NUMERIC_RE.match(nxt)
                    or HUV_TZH_RE.match(nxt)
                    or SUT_NUMERIC_RE.match(nxt)
                    or nxt.startswith("Toplam")
                ):
                    break
                name_parts.append(nxt)
                m += 1
            name = re.sub(r"\s+", " ", " ".join(name_parts)).strip()
            procs.append(PopupProcedure(code=code, name=name))
            # İsimden sonraki tutar satırlarını atla.
            while m < len(block) and (AMOUNT_RE.match(block[m].strip()) or INT_ONLY_RE.match(block[m].strip())):
                m += 1
            k = m
        else:
            k += 1
    return procs


def parse_popup_text(text: str) -> PopupData:
    lines = [l.rstrip() for l in text.splitlines()]
    data = PopupData(raw_text=text)

    data.provizyon_no = _field(text, r"Provizyon No:\s*(\d+)")
    data.uye_sicil = _field(text, r"Üye Sicil No:\s*(\S+)")
    data.tc = _field(text, r"TC Kimlik No[:\s]+(\d{11})")
    data.dogum_tarihi = _field(text, r"Doğum Tarihi[:\s]+(\d{2}-\d{2}-\d{4})")
    data.hizmet_zamani = _field(text, r"Provizyon/Hizmet Zamanı:\s*([\d-]+\s+[\d:]+)")
    data.uye_statu = _field(text, r"Üye Statü:\s*(.+)")
    data.hizmet_alan = _field(text, r"Hizmet Alan:\s*(.+)")

    # Kurum adı: "HİZMET DÖKÜM FORMU" satırının hemen ardındaki satır.
    for i, line in enumerate(lines):
        if "HİZMET DÖKÜM FORMU" in line and i + 1 < len(lines):
            cand = lines[i + 1].strip()
            if cand and "Üye Sicil" not in cand:
                data.kurum = cand
            break

    data.hasta_ad = _parse_hasta_ad(lines)
    data.diagnoses = _parse_diagnoses(lines)
    data.procedures = _parse_procedures(lines)

    if not data.provizyon_no:
        data.warnings.append("Provizyon No bulunamadı.")
    if not data.hasta_ad:
        data.warnings.append("Hasta adı bulunamadı.")
    if not data.diagnoses:
        data.warnings.append("Üst tanı (ICD-10) bulunamadı.")
    if not data.procedures:
        data.warnings.append("Hizmet kodu (HUV) bulunamadı.")
    return data


def parse_popup_pdf(path: str | Path) -> PopupData:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Popup PDF bulunamadı: {path}")
    return parse_popup_text(_extract_text(path))

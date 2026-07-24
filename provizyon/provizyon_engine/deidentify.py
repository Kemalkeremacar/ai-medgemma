"""PHI-safe de-identification utilities for provizyon data exports.

Provides pseudonymization, age banding, and PHI field enforcement
for bulk data extraction and business impact analysis artifacts.

All AI/prefill artifacts must go through this module — raw PHI values
(TC kimlik, ad/soyad, sicil, belge yolu, açık kurum/doktor adı)
must never appear in output files.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any


_DEFAULT_SALT: bytes | None = None


def _get_salt() -> bytes:
    global _DEFAULT_SALT
    if _DEFAULT_SALT is None:
        _DEFAULT_SALT = os.urandom(32)
    return _DEFAULT_SALT


def init_salt(salt: bytes | None = None) -> bytes:
    """Initialize or reset the module-level hash salt.

    Call once at the start of a run. If *salt* is None a fresh random
    salt is generated. Returns the active salt (never written to disk).
    """
    global _DEFAULT_SALT
    _DEFAULT_SALT = salt if salt is not None else os.urandom(32)
    return _DEFAULT_SALT


def pseudonymize(value: int | str, *, salt: bytes | None = None) -> str:
    """Return a 16-char hex pseudonym for *value*."""
    s = salt if salt is not None else _get_salt()
    raw = f"{value}".encode() + s
    return hashlib.sha256(raw).hexdigest()[:16]


def age_band(age: int | None) -> str:
    """Map exact age to a privacy-safe band."""
    if age is None:
        return "unknown"
    if age < 2:
        return "0-1"
    if age <= 17:
        return "2-17"
    if age <= 30:
        return "18-30"
    if age <= 45:
        return "31-45"
    if age <= 60:
        return "46-60"
    if age <= 75:
        return "61-75"
    return "76+"


# ── PHI blacklist ────────────────────────────────────────────────────────

PHI_BLACKLISTED_FIELDS: frozenset[str] = frozenset({
    "TCKimlik",
    "HastaAd",
    "HastaSoyad",
    "UyeSicil",
    "UyeId",
    "PersonelID",
    "AkrabaID",
    "KurumAdi",
    "DoktorAdi",
    "BelgeBilgileri",
    "DosyaYolu",
    "DosyaAd",
})

SAFE_PROJECTION_MAP: dict[str, str] = {
    "ProvizyonId": "case_id",
    "HastaYas": "age_band",
    "Cinsiyet": "sex",
    "KurumTipi": "kurum_tipi",
    "Il": "il",
    "IslemTipi": "islem_tipi",
    "Brans": "brans",
    "HizmetTarih": "provision_period",
    "ProvizyonDurumu": "before_decision_status",
    "ProvizyonDurumId": "provizyon_durum_id",
    "ProvizyonTipi": "provizyon_tipi",
    "TaniBilgileri": "diagnosis_codes",
    "IslemBilgileri": "procedure_codes",
}


def project_row_safe(
    row: dict[str, Any],
    *,
    salt: bytes | None = None,
) -> dict[str, Any]:
    """Project a raw S_VW_PROVIZYON_AI row into a PHI-safe dict.

    - ProvizyonId → pseudonymized case_id
    - KurumAdi → pseudonymized kurum_key_hash  (category kept via KurumTipi)
    - DoktorAdi → pseudonymized doktor_key_hash (branş kept via Brans)
    - HastaYas → age_band
    - PHI fields stripped
    - BelgeBilgileri → has_documents boolean flag
    """
    s = salt if salt is not None else _get_salt()

    pid = row.get("ProvizyonId")
    safe: dict[str, Any] = {
        "case_id": pseudonymize(pid, salt=s) if pid is not None else "",
    }

    hizmet = row.get("HizmetTarih")
    if hizmet is not None:
        if hasattr(hizmet, "strftime"):
            safe["provision_period"] = hizmet.strftime("%Y-%m")
        else:
            safe["provision_period"] = str(hizmet)[:7]
    else:
        safe["provision_period"] = ""

    safe["age_band"] = age_band(
        int(row["HastaYas"]) if row.get("HastaYas") is not None else None,
    )
    safe["sex"] = row.get("Cinsiyet") or ""

    kurum_adi = row.get("KurumAdi")
    safe["kurum_key_hash"] = pseudonymize(kurum_adi, salt=s) if kurum_adi else ""
    safe["kurum_tipi"] = row.get("KurumTipi") or ""

    safe["il"] = row.get("Il") or ""

    doktor_adi = row.get("DoktorAdi")
    safe["doktor_key_hash"] = pseudonymize(doktor_adi, salt=s) if doktor_adi else ""
    safe["brans"] = row.get("Brans") or ""

    safe["islem_tipi"] = row.get("IslemTipi") or ""
    safe["provizyon_tipi"] = row.get("ProvizyonTipi") or ""
    safe["provizyon_durumu"] = row.get("ProvizyonDurumu") or ""
    safe["provizyon_durum_id"] = row.get("ProvizyonDurumId")

    safe["has_documents"] = bool(row.get("BelgeBilgileri"))

    return safe


def validate_no_phi(record: dict[str, Any]) -> list[str]:
    """Return a list of PHI field names found in *record* (should be empty)."""
    return [k for k in record if k in PHI_BLACKLISTED_FIELDS]

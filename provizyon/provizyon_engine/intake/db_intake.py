"""MSSQL veritabanından (dbo.S_VW_PROVIZYON_AI view) ProvizyonJob üretir.

Kullanım:
    python -m provizyon_engine.intake.db_intake 138 [--enqueue] [--json]
    python -m provizyon_engine.intake.db_intake --pending [--enqueue]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from .. import settings
from ..db import get_connection
from ..models import Cinsiyet, DocumentInput, ProcedureInput, ProvizyonJob

log = logging.getLogger(__name__)

AI_REVIEW_STATUS_ID = 5

_VIEW_SQL = "SELECT * FROM dbo.S_VW_PROVIZYON_AI WHERE ProvizyonId = ?"

_PENDING_SQL = "SELECT * FROM dbo.S_VW_PROVIZYON_AI WHERE ProvizyonDurumId = ?"

_BELGE_TIP_MAP: dict[str, str] = {
    "rapor / epikriz": "epikriz",
    "rapor": "rapor",
    "doktor raporu": "rapor",
    "fatura": "fatura",
    "küpür": "fatura",
    "tetkik sonuçları": "rapor",
    "reçete": "rapor",
    "fizik tedavi": "rapor",
    "ön onay": "rapor",
    "rakam onay": "rapor",
    "uzatma": "rapor",
    "günübirlik": "rapor",
    "operasyon": "rapor",
    "paket ameliyat": "rapor",
    "ayakta tedavi": "rapor",
    "diş": "rapor",
    "optik": "rapor",
    "lens": "rapor",
    "seanslı": "rapor",
    "doğum": "rapor",
}

_FILE_READ_SQL = "SELECT BulkColumn FROM OPENROWSET(BULK '{}', SINGLE_BLOB) AS doc"


def _cinsiyet_from_db(value: str | None) -> Cinsiyet:
    if not value:
        return Cinsiyet.BILINMIYOR
    v = value.strip().lower()
    if v in ("erkek", "e"):
        return Cinsiyet.ERKEK
    if v in ("kadın", "kadin", "k"):
        return Cinsiyet.KADIN
    return Cinsiyet.BILINMIYOR


def _row_to_dict(cursor, row) -> dict[str, Any]:
    return {col[0]: val for col, val in zip(cursor.description, row)}


def _parse_diagnoses(tani_bilgileri: str | None) -> list[str]:
    """View'daki TaniBilgileri kolonunu parse eder: kod|ad<~>kod|ad -> [kod, ...]"""
    if not tani_bilgileri:
        return []
    codes: list[str] = []
    for entry in tani_bilgileri.split("<~>"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split("|")
        if parts[0]:
            codes.append(parts[0])
    return codes


def _parse_procedures(islem_bilgileri: str | None) -> tuple[list[ProcedureInput], str | None]:
    """View'daki IslemBilgileri kolonunu parse eder: kod|ad<~>kod|ad ..."""
    if not islem_bilgileri:
        return [], None
    procedures: list[ProcedureInput] = []
    code_family: str | None = None
    for entry in islem_bilgileri.split("<~>"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split("|")
        if len(parts) < 2:
            continue
        kod, ad = parts[0], parts[1]
        liste_tip = parts[2] if len(parts) >= 3 else ""
        code_type = "HUV" if liste_tip == "HUV" else "SUT" if "SUT" in (liste_tip or "") else "auto"
        procedures.append(ProcedureInput(code=kod, code_type=code_type, name=ad))
        if code_family is None and code_type in ("HUV", "SUT"):
            code_family = code_type
    return procedures, code_family


def _map_belge_tip(belge_tipi: str) -> str | None:
    """S_BELGE_TIP_TANIM.Ad -> pipeline doc_type."""
    key = belge_tipi.strip().lower()
    for pattern, dtype in _BELGE_TIP_MAP.items():
        if pattern in key:
            return dtype
    return None


def _download_documents_from_view(
    cursor,
    belge_bilgileri: str | None,
    provizyon_id: int,
    *,
    patient_name: str | None = None,
    declared_hasta_id: str | None = None,
) -> list[DocumentInput]:
    """View'daki BelgeBilgileri kolonunu parse eder, OPENROWSET ile indirir."""

    if not belge_bilgileri:
        return []

    doc_dir = settings.DOCUMENT_ROOT / str(provizyon_id)
    doc_dir.mkdir(parents=True, exist_ok=True)

    documents: list[DocumentInput] = []
    for entry in belge_bilgileri.split("<~>"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split("|")
        if len(parts) < 4:
            log.warning("Beklenmeyen belge formatı: %s", entry)
            continue

        full_path, filename, ext, belge_tipi = parts[0], parts[1], parts[2], parts[3]
        if not full_path:
            continue

        safe_name = f"{filename or 'belge'}{ext or ''}"
        safe_name = "".join(c if c.isalnum() or c in ".-_ ()" else "_" for c in safe_name)
        local_path = doc_dir / safe_name
        if local_path.exists() and local_path.stat().st_size > 0:
            log.debug("Belge zaten mevcut, atlanıyor: %s", local_path)
        else:
            try:
                sql = _FILE_READ_SQL.format(full_path.replace("'", "''"))
                cursor.execute(sql)
                blob_row = cursor.fetchone()
                if blob_row and blob_row[0]:
                    local_path.write_bytes(blob_row[0])
                    log.info("Belge indirildi: %s (%d bytes)", local_path.name, len(blob_row[0]))
                else:
                    log.warning("Belge boş döndü: %s", full_path)
                    continue
            except Exception as exc:
                log.warning("Belge indirilemedi (%s): %s", full_path, exc)
                continue

        doc_type = _map_belge_tip(belge_tipi)

        documents.append(DocumentInput(
            path=str(local_path),
            doc_type=doc_type,
            doc_type_confidence="medium" if doc_type else None,
            doc_type_source="db" if doc_type else None,
            title=belge_tipi,
            declared_patient_name=patient_name,
            declared_hasta_id=declared_hasta_id,
        ))

    return documents


def _build_job_from_row(
    cursor, row_dict: dict[str, Any], *, skip_documents: bool = False
) -> ProvizyonJob:
    pid = row_dict["ProvizyonId"]

    diagnoses = _parse_diagnoses(row_dict.get("TaniBilgileri"))
    procedures, code_family = _parse_procedures(row_dict.get("IslemBilgileri"))

    hasta_ad = row_dict.get("HastaAd") or ""
    hasta_soyad = row_dict.get("HastaSoyad") or ""
    patient_name = f"{hasta_ad} {hasta_soyad}".strip() or None

    yas_raw = row_dict.get("HastaYas")
    yas = int(yas_raw) if yas_raw is not None else None

    tc_kimlik = row_dict.get("TCKimlik")
    uye_sicil = row_dict.get("UyeSicil")
    hasta_id = uye_sicil or tc_kimlik or str(row_dict.get("UyeId") or "")

    notes: list[str] = []
    kurum = row_dict.get("KurumAdi")
    if kurum:
        notes.append(f"Kurum: {kurum}")
    brans = row_dict.get("Brans")
    if brans:
        notes.append(f"Branş: {brans}")
    doktor = row_dict.get("DoktorAdi")
    if doktor:
        notes.append(f"Doktor: {doktor}")
    tani_bilgileri = row_dict.get("TaniBilgileri")
    if tani_bilgileri:
        tani_strs = [e.replace("|", " - ") for e in tani_bilgileri.split("<~>") if e.strip()]
        notes.append(f"Tanılar: {', '.join(tani_strs)}")

    if skip_documents:
        # Belgesiz mod: OPENROWSET ile dosya indirmeyi tamamen atla; belge katmanları
        # orkestratörde SKIPPED işaretlenir, belge yokluğu hata sayılmaz.
        documents: list[DocumentInput] = []
        documents_mode: str | None = "skipped_full_pipeline"
    else:
        documents = _download_documents_from_view(
            cursor, row_dict.get("BelgeBilgileri"),
            pid,
            patient_name=patient_name,
            declared_hasta_id=hasta_id,
        )
        documents_mode = None

    huv_codes = [p.code for p in procedures if p.code_type == "HUV"]
    sut_codes = [p.code for p in procedures if p.code_type == "SUT"]

    return ProvizyonJob(
        provizyon_id=str(pid),
        hasta_id=hasta_id,
        tc_kimlik=tc_kimlik,
        patient_name=patient_name,
        yas=yas,
        cinsiyet=_cinsiyet_from_db(row_dict.get("Cinsiyet")),
        facility_level=row_dict.get("KurumTipi"),
        code_family=code_family,
        huv_codes=huv_codes,
        sut_codes=sut_codes,
        procedures=procedures,
        diagnoses=diagnoses,
        documents=documents,
        notes=notes,
        institution_name=kurum,
        documents_mode=documents_mode,
    )


def fetch_provizyon(provizyon_id: int, *, skip_documents: bool = False) -> ProvizyonJob:
    """Tek bir provizyonu DB'den çekip ProvizyonJob olarak döner."""

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(_VIEW_SQL, provizyon_id)
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"Provizyon bulunamadı: {provizyon_id}")
        row_dict = _row_to_dict(cursor, row)
        return _build_job_from_row(cursor, row_dict, skip_documents=skip_documents)


def fetch_pending_provizyonlar(
    durum_id: int = AI_REVIEW_STATUS_ID,
    *,
    skip_documents: bool = False,
    limit: int | None = None,
) -> list[ProvizyonJob]:
    """Belirtilen durumdaki (varsayılan: AI incelemesi bekleyen) provizyonları döner.

    ``limit`` verilirse en fazla o kadar kayıt döner (sunum hızı için).
    ``skip_documents`` ile belge indirme atlanır (belgesiz tam akış).
    """

    jobs: list[ProvizyonJob] = []
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(_PENDING_SQL, durum_id)
        cols = [col[0] for col in cursor.description]
        row_dicts = [dict(zip(cols, row)) for row in cursor.fetchall()]
        if limit is not None and limit > 0:
            row_dicts = row_dicts[:limit]
        for row_dict in row_dicts:
            try:
                jobs.append(
                    _build_job_from_row(cursor, row_dict, skip_documents=skip_documents)
                )
            except Exception as exc:
                log.warning("Provizyon %s okunamadı: %s", row_dict.get("ProvizyonId"), exc)
    return jobs


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MSSQL'den provizyon çek / kuyruğa ekle.")
    ap.add_argument("provizyon_id", nargs="?", type=int, help="Provizyon ID.")
    ap.add_argument("--pending", action="store_true", help="AI incelemesi bekleyen tüm provizyonları çek.")
    ap.add_argument("--enqueue", action="store_true", help="Üretilen işleri Redis kuyruğuna ekle.")
    ap.add_argument("--json", action="store_true", help="JSON olarak yazdır.")
    args = ap.parse_args(argv)

    if not args.provizyon_id and not args.pending:
        ap.error("provizyon_id veya --pending gerekli.")

    try:
        if args.pending:
            jobs = fetch_pending_provizyonlar()
            print(f"{len(jobs)} adet AI incelemesi bekleyen provizyon bulundu.")
        else:
            jobs = [fetch_provizyon(args.provizyon_id)]
    except Exception as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 1

    for job in jobs:
        if args.json:
            print(json.dumps(job.model_dump(mode="json"), ensure_ascii=False, indent=2))
        else:
            print(f"Provizyon : {job.provizyon_id}")
            print(f"Hasta     : {job.patient_name} (tc={job.tc_kimlik}, yaş={job.yas}, cinsiyet={job.cinsiyet.value})")
            print(f"Kodlar    : {job.code_family} | {[p.code for p in job.procedures]}")
            print(f"Tanılar   : {job.diagnoses}")
            print(f"Notlar    : {job.notes}")
            print()

    if args.enqueue:
        from ..queue.redis_queue import RedisQueue

        queue = RedisQueue()
        if not queue.ping():
            print("HATA: Redis kuyruğuna erişilemiyor.", file=sys.stderr)
            return 2
        for job in jobs:
            added = queue.enqueue(job.provizyon_id, job.model_dump(mode="json"))
            status = "eklendi" if added else "zaten kuyrukta"
            print(f"Kuyruk: {job.provizyon_id} -> {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

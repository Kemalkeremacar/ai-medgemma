"""Bir provizyon klasöründen otomatik ``ProvizyonJob`` üretir.

Klasör beklenen içerik:
  - Bir "Hizmet Döküm Formu" popup PDF'i (dosya adında ``PopupPage`` geçer):
    hasta/tanı/işlem kaynağı.
  - Bir veya daha fazla ekli belge (PDF/görsel/metin): epikriz, rapor, fatura...

Kullanım (CLI):
    python -m provizyon_engine.intake.folder_intake <klasör> [--enqueue] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from ..models import Cinsiyet, DocumentInput, ProcedureInput, ProvizyonJob
from ..documents.classify import (
    classify_document,
    gender_from_hizmet_alan,
    peek_file_text,
    infer_gender_from_text,
)
from .popup_parser import HUV_NUMERIC_RE, PopupData, SUT_NUMERIC_RE, parse_popup_pdf

DOC_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".txt", ".md"}


def find_popup_pdf(folder: Path) -> Path | None:
    candidates = sorted(folder.glob("*"))
    for f in candidates:
        if f.is_file() and "popuppage" in f.name.lower():
            return f
    return None


def collect_documents(
    folder: Path,
    popup: Path | None,
    *,
    declared_hasta_id: str | None = None,
    declared_patient_name: str | None = None,
    procedure_names: list[str] | None = None,
) -> list[DocumentInput]:
    docs: list[DocumentInput] = []
    for f in sorted(folder.glob("*")):
        if not f.is_file():
            continue
        if popup is not None and f.resolve() == popup.resolve():
            continue
        if f.suffix.lower() not in DOC_SUFFIXES:
            continue
        sample_plain = peek_file_text(f, max_pages=2, ocr_if_empty=False)
        sample = sample_plain if len(sample_plain.strip()) >= 15 else peek_file_text(f, max_pages=2, ocr_if_empty=True)
        source = "peek" if len(sample_plain.strip()) >= 15 else "ocr_peek"
        guess = classify_document(
            f.name,
            sample or None,
            procedure_names=procedure_names,
            source=source,
        )
        docs.append(DocumentInput(
            path=str(f.resolve()),
            doc_type=guess.doc_type if guess else None,
            doc_type_confidence=guess.confidence if guess else None,
            doc_type_source=guess.source if guess else None,
            declared_hasta_id=declared_hasta_id,
            declared_patient_name=declared_patient_name,
        ))
    return docs


def _resolve_cinsiyet(popup_data: PopupData, folder: Path, popup: Path) -> Cinsiyet:
    cinsiyet = gender_from_hizmet_alan(popup_data.hizmet_alan)
    if cinsiyet != Cinsiyet.BILINMIYOR:
        return cinsiyet
    # Popup'ta "Kendisi" gibi değerlerde epikriz/fatura metninden cinsiyet oku.
    priority = ("epikriz", "fatura", "rapor", "hasta")
    files = [f for f in folder.glob("*") if f.is_file() and f.resolve() != popup.resolve()]
    files.sort(
        key=lambda p: next(
            (i for i, key in enumerate(priority) if key in p.name.lower()),
            len(priority),
        )
    )
    for f in files:
        if f.suffix.lower() not in DOC_SUFFIXES:
            continue
        inferred = infer_gender_from_text(peek_file_text(f, max_pages=6))
        if inferred is not None:
            return inferred
    return Cinsiyet.BILINMIYOR


def _age_from_birthdate(dogum: str | None, ref: str | None) -> int | None:
    if not dogum:
        return None
    try:
        bd = datetime.strptime(dogum, "%d-%m-%Y").date()
    except ValueError:
        return None
    ref_date: date
    if ref:
        try:
            ref_date = datetime.strptime(ref.split()[0], "%d-%m-%Y").date()
        except (ValueError, IndexError):
            ref_date = date.today()
    else:
        ref_date = date.today()
    years = ref_date.year - bd.year - ((ref_date.month, ref_date.day) < (bd.month, bd.day))
    return years if years >= 0 else None


def build_job(
    popup_data: PopupData,
    documents: list[DocumentInput],
    *,
    fallback_id: str,
    cinsiyet: Cinsiyet | None = None,
) -> ProvizyonJob:
    provizyon_id = popup_data.provizyon_no or fallback_id
    age = _age_from_birthdate(popup_data.dogum_tarihi, popup_data.hizmet_zamani)

    # Sayısal HUV kodları motorlara gider; tüm kalemler (TZH dahil) izlenebilirlik
    # için procedures listesinde adlarıyla tutulur.
    procedures = [
        ProcedureInput(
            code=p.code,
            code_type=(
                "SUT"
                if SUT_NUMERIC_RE.match(p.code)
                else "HUV"
                if HUV_NUMERIC_RE.match(p.code)
                else "auto"
            ),
            name=p.name or None,
        )
        for p in popup_data.procedures
    ]

    notes: list[str] = []
    if popup_data.kurum:
        notes.append(f"Kurum: {popup_data.kurum}")
    if popup_data.diagnoses:
        notes.append(
            "Üst tanılar: "
            + "; ".join(f"{d.code} {d.name}".strip() for d in popup_data.diagnoses)
        )
    notes.extend(popup_data.warnings)

    code_family = None
    if popup_data.sut_codes and not popup_data.huv_codes:
        code_family = "SUT"
    elif popup_data.huv_codes:
        code_family = "HUV"

    return ProvizyonJob(
        provizyon_id=provizyon_id,
        hasta_id=popup_data.uye_sicil or popup_data.tc,
        tc_kimlik=popup_data.tc,
        patient_name=popup_data.hasta_ad,
        yas=age,
        cinsiyet=cinsiyet if cinsiyet is not None else gender_from_hizmet_alan(popup_data.hizmet_alan),
        code_family=code_family,
        huv_codes=popup_data.huv_codes,
        sut_codes=popup_data.sut_codes,
        procedures=procedures,
        diagnoses=popup_data.icd_codes,
        documents=documents,
        notes=notes,
    )


def build_job_from_folder(folder: str | Path) -> ProvizyonJob:
    folder = Path(folder)
    if not folder.is_dir():
        raise NotADirectoryError(f"Klasör bulunamadı: {folder}")
    popup = find_popup_pdf(folder)
    if popup is None:
        raise FileNotFoundError(
            f"Klasörde popup (Hizmet Döküm Formu) PDF'i bulunamadı: {folder}"
        )
    popup_data = parse_popup_pdf(popup)
    procedure_names = [p.name for p in popup_data.procedures if p.name]
    documents = collect_documents(
        folder,
        popup,
        declared_hasta_id=popup_data.uye_sicil or popup_data.tc,
        declared_patient_name=popup_data.hasta_ad,
        procedure_names=procedure_names,
    )
    cinsiyet = _resolve_cinsiyet(popup_data, folder, popup)
    return build_job(popup_data, documents, fallback_id=folder.name, cinsiyet=cinsiyet)


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Provizyon klasöründen iş üret/kuyruğa ekle.")
    ap.add_argument("folder", help="Provizyon klasörü (popup PDF + belgeler).")
    ap.add_argument("--enqueue", action="store_true", help="Üretilen işi Redis kuyruğuna ekle.")
    ap.add_argument("--json", action="store_true", help="İşi JSON olarak yazdır.")
    args = ap.parse_args(argv)

    try:
        job = build_job_from_folder(args.folder)
    except Exception as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(job.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print(f"Provizyon : {job.provizyon_id}")
        print(f"Hasta     : {job.patient_name} (id={job.hasta_id}, yaş={job.yas}, cinsiyet={job.cinsiyet.value})")
        print(f"HUV kodlar: {job.huv_codes}")
        print(f"SUT kodlar: {job.sut_codes}")
        print(f"Tanılar   : {job.diagnoses}")
        print(f"Belgeler  : {len(job.documents)} adet")
        for d in job.documents:
            print(f"   - {Path(d.path).name} ({d.doc_type or 'tip?'})")

    if args.enqueue:
        from ..queue.redis_queue import RedisQueue  # noqa: PLC0415

        queue = RedisQueue()
        if not queue.ping():
            print("HATA: Redis kuyruğuna erişilemiyor.", file=sys.stderr)
            return 2
        queue.enqueue(job.provizyon_id, job.model_dump(mode="json"))
        print(f"Kuyruğa eklendi: {job.provizyon_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

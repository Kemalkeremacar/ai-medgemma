#!/usr/bin/env python3
"""Gerçek provizyon klasörlerinde pipeline'ı aşama aşama denetler.

Kullanım:
    cd provizyon
    .venv/bin/python scripts/run_pipeline_audit.py [klasör...]
    .venv/bin/python scripts/run_pipeline_audit.py --all

MedGemma çağrılmaz (deterministik katmanlar + kanıt paketi hazırlığı).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# provizyon_engine paket kökü
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from provizyon_engine import settings
from provizyon_engine.decision import merge_decisions
from provizyon_engine.documents import requirement as req_mod
from provizyon_engine.documents.classify import infer_gender_from_documents, refine_doc_types
from provizyon_engine.documents.extract import extract_document
from provizyon_engine.documents.ocr import ocr_document
from provizyon_engine.documents.patient_match import match_documents
from provizyon_engine.documents.prepare import build_evidence_package
from provizyon_engine.documents.source import FilesystemDocumentSource
from provizyon_engine.engines.diagnosis import check_diagnoses
from provizyon_engine.engines.sut_diagnosis import check_sut_diagnoses
from provizyon_engine.engines.sut_rules import check_sut_rules
from provizyon_engine.intake.folder_intake import build_job_from_folder
from provizyon_engine.models import Cinsiyet, LayerStatus


def _layer_dict(layer) -> dict | None:
    if layer is None:
        return None
    return {
        "status": layer.status.value,
        "message": layer.message,
        "detail": layer.detail,
    }


def audit_folder(folder: Path) -> dict:
    job = build_job_from_folder(folder)
    source = FilesystemDocumentSource()
    refs = source.resolve_all(job.documents)
    existing = [r for r in refs if r.exists]
    missing = [r for r in refs if not r.exists]

    extracted = []
    doc_stats = []
    for ref in existing:
        doc = extract_document(ref, render_images=True)
        doc = ocr_document(doc)
        extracted.append(doc)
        doc_stats.append({
            "file": ref.path.name,
            "doc_type": ref.doc_type,
            "pages": len(doc.pages),
            "text_chars": len(doc.combined_text),
            "ocr_pages": sum(1 for p in doc.pages if p.needs_ocr),
            "error": doc.error,
        })

    refine_notes = refine_doc_types(extracted)
    if job.cinsiyet == Cinsiyet.BILINMIYOR:
        inferred = infer_gender_from_documents(extracted)
        if inferred is not None:
            job.cinsiyet = inferred
    for stat, doc in zip(doc_stats, extracted):
        stat["doc_type"] = doc.ref.doc_type

    belge_hasta = match_documents(job, extracted) if existing else None
    zorunlu = req_mod.check_requirement(job, documents_present=bool(existing))
    code_source = job.diagnosis_code_source()
    tani = None
    sut_tani = None
    if code_source in ("huv", "both"):
        tani = check_diagnoses(job.all_huv_codes(), job.diagnoses)
    if code_source in ("sut", "both"):
        sut_tani = check_sut_diagnoses(job)
    sut = check_sut_rules(job, use_qdrant=True)

    evidence = build_evidence_package(
        extracted,
        include_images=True,
        huv_codes=job.all_huv_codes(),
        sut_codes=job.all_sut_codes(),
        icd_codes=job.diagnoses,
        patient_name=job.patient_name,
    )

    outcome = merge_decisions(
        belge_hasta=belge_hasta,
        zorunlu_evrak=zorunlu,
        tani_kurali=tani,
        sut_tani_kurali=sut_tani,
        sut_kurali=sut,
        medgemma=None,
        medgemma_layer=None,
        document_analysis_failed=False,
    )

    return {
        "folder": folder.name,
        "provizyon_id": job.provizyon_id,
        "hasta": {
            "ad": job.patient_name,
            "hasta_id": job.hasta_id,
            "tc_kimlik": job.tc_kimlik,
            "yas": job.yas,
            "cinsiyet": job.cinsiyet.value,
        },
        "huv_codes": job.all_huv_codes(),
        "sut_codes": job.all_sut_codes(),
        "diagnosis_code_source": code_source,
        "code_family": job.code_family,
        "diagnoses": job.diagnoses,
        "belgeler": {
            "toplam": len(job.documents),
            "bulunan": len(existing),
            "eksik": len(missing),
            "detay": doc_stats,
        },
        "asamalar": {
            "belge_hasta": _layer_dict(belge_hasta),
            "zorunlu_evrak": _layer_dict(zorunlu),
            "tani_kurali": _layer_dict(tani),
            "sut_tani_kurali": _layer_dict(sut_tani),
            "sut_kurali": _layer_dict(sut),
        },
        "evidence": {
            "text_chars": len(evidence.text_evidence),
            "images": len(evidence.image_paths),
            "selected_pages": evidence.selected_page_numbers,
            "notes": evidence.notes,
        },
        "nihai_karar_deterministik": {
            "karar": outcome.karar.value,
            "gerekce": outcome.gerekce,
            "warnings": outcome.warnings,
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Provizyon pipeline aşama denetimi")
    ap.add_argument("folders", nargs="*", help="Provizyon klasör yolları")
    ap.add_argument("--all", action="store_true", help=f"Tüm intake klasörlerini tara ({settings.INTAKE_WATCH_DIR})")
    ap.add_argument("--json", action="store_true", help="JSON çıktı")
    args = ap.parse_args(argv)

    req_mod._OVERRIDES = None
    req_mod._PREFIX_RULES = None
    req_mod._REQUIRED_DOC_CODES = None

    folders: list[Path] = []
    if args.all:
        watch = settings.INTAKE_WATCH_DIR
        folders = sorted(p for p in watch.iterdir() if p.is_dir())
    else:
        for raw in args.folders:
            folders.append(Path(raw).resolve())

    if not folders:
        ap.print_help()
        return 1

    results = []
    for folder in folders:
        try:
            results.append(audit_folder(folder))
        except Exception as exc:
            results.append({"folder": folder.name, "error": str(exc)})

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            if "error" in r:
                print(f"\n=== {r['folder']} === HATA: {r['error']}")
                continue
            print(f"\n{'='*60}")
            print(f"  {r['folder']} | {r['provizyon_id']} | {r['hasta']['ad']}")
            print(f"  HUV: {', '.join(r['huv_codes'])}")
            print(f"  Tanı: {', '.join(r['diagnoses'])}")
            print(f"  Belgeler: {r['belgeler']['bulunan']}/{r['belgeler']['toplam']}")
            for d in r["belgeler"]["detay"]:
                print(f"    - {d['file']}: {d['pages']} sayfa, {d['text_chars']} kar, OCR={d['ocr_pages']}, tip={d['doc_type']}")
            for name, layer in r["asamalar"].items():
                if layer:
                    print(f"  [{name}] {layer['status']}: {layer['message'][:120]}")
            ev = r["evidence"]
            print(f"  [evidence] {ev['text_chars']} kar metin, {ev['images']} gorsel, sayfalar={ev['selected_pages']}")
            for n in ev.get("notes", []):
                print(f"    Not: {n}")
            nk = r["nihai_karar_deterministik"]
            print(f"  >>> Nihai (MedGemma haric): {nk['karar']} — {nk['gerekce'][:100]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Demo: SİNCAN KORU HASTANESİ + 06.42499 DERİ PRİCK TESTİ ödeme eğilimi sinyali.

Adımlar:
  1. Pilot sinyal payload'ını oluştur (eski payer_trend_profiles.json'dan)
  2. TEI embedding + Qdrant diagnosis_procedure_pilot'a upsert
  3. Test ProvizyonJob oluştur (belgesiz, deterministik katmanlar + ödeme sinyali)
  4. Pipeline'ı çalıştır
  5. Beklenen sonucu doğrula: manuel_inceleme / orange / payer_payment_tendency

Kullanım:
    cd provizyon
    .venv/bin/python scripts/demo_sincan_koru_payer_trend.py
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from provizyon_engine import _sut_bootstrap  # noqa: F401
from provizyon_engine import settings

SIGNAL_PAYLOAD = {
    "signal_type": "diagnosis_payment_overlay",
    "source_stage": "provizyon",
    "institution_key": "322001",
    "institution_name": "SİNCAN KORU HASTANESİ",
    "claim_channel": "provider_or_direct_billing",
    "institution_context": "contracted_or_unknown_provider",
    "trend_scope": "medical_service_code",
    "diagnosis_code": "L50.0",
    "diagnosis_name": "Allerjik ürtiker",
    "diagnosis_parent": "DERMATOLOJİ",
    "procedure_code": "06.42499",
    "procedure_name": "DERİ PRİCK TESTİ (HERBİRİ)",
    "list_type": "TTB",
    "coverage": "DERMATOLOJİ",
    "sample_size": 40,
    "unique_service_count": 40,
    "rejected_cases": 20,
    "case_rejection_rate": 0.50,
    "amount_rejection_rate": 1.0,
    "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "rule_engine_signal": None,
    "risk_level": "orange",
    "risk_score": 64,
    "confidence": 0.9445,
    "distinct_procedure_count": 1,
    "top_procedures": [
        {
            "procedure_code": "06.42499",
            "procedure_name": "DERİ PRİCK TESTİ (HERBİRİ)",
            "list_type": "TTB",
            "coverage": "DERMATOLOJİ",
            "case_count": 40,
            "procedure_share": 1.0,
            "sample_size": 40,
            "unique_service_count": 40,
            "total_amount": 0.0,
            "rejected_amount": 0.0,
            "returned_amount": 0.0,
            "rejected_cases": 20,
            "case_rejection_rate": 0.50,
            "amount_rejection_rate": 1.0,
        }
    ],
}

EMBED_TEXT = (
    "Sinyal: Kurum tanı-işlem ödeme eğilimi\n"
    "Kurum: SİNCAN KORU HASTANESİ\n"
    "Kanal: provider_or_direct_billing\n"
    "Kapsam: medical_service_code\n"
    "Tanı: L50.0 Allerjik ürtiker\n"
    "İşlem: 06.42499 DERİ PRİCK TESTİ (HERBİRİ)\n"
    "Örneklem: 40 (tekil hizmet 40)\n"
    "Red vaka: 20\n"
    "Vaka red oranı: 0.50\n"
    "Tutar red oranı: 1.0\n"
    "Risk: orange (skor 64)"
)

TEST_JOB_DICT = {
    "provizyon_id": f"DEMO-SINCAN-{int(time.time())}",
    "hasta_id": "DEMO-H-001",
    "tc_kimlik": "11111111110",
    "patient_name": "DEMO HASTA",
    "yas": 35,
    "cinsiyet": "kadin",
    "institution_name": "SİNCAN KORU HASTANESİ",
    "institution_key": "322001",
    "code_family": "HUV",
    "procedures": [
        {
            "code": "06.42499",
            "code_type": "HUV",
            "name": "DERİ PRİCK TESTİ (HERBİRİ)",
            "quantity": 1,
            "payment_procedure_code": "06.42499",
            "payment_procedure_name": "DERİ PRİCK TESTİ (HERBİRİ)",
        }
    ],
    "diagnoses": ["L50.0"],
    "documents": [],
}


def _section(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def step1_upsert_signal() -> str:
    """Pilot sinyal payload'ını Qdrant'a upsert eder. Dönen point_id."""

    _section("ADIM 1: Sinyal payload'ını Qdrant'a yükle")

    from sut_engine.embedding_client import EmbeddingClient, EmbeddingConfig

    embedder = EmbeddingClient(
        EmbeddingConfig(base_url=settings.TEI_URL, dim=settings.EMBEDDING_DIM)
    )
    vector = embedder.embed_one(EMBED_TEXT)
    print(f"  Embedding boyutu: {len(vector)}")

    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct

    client = QdrantClient(url=settings.QDRANT_URL, timeout=60, check_compatibility=False)
    collection = settings.DIAGNOSIS_PROCEDURE_COLLECTION

    info = client.get_collection(collection)
    print(f"  Collection: {collection}  (mevcut nokta: {info.points_count})")

    payload_with_text = dict(SIGNAL_PAYLOAD)
    payload_with_text["text"] = EMBED_TEXT

    point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sincan-koru-06.42499-L50.0-demo"))
    client.upsert(
        collection_name=collection,
        points=[
            PointStruct(
                id=point_id,
                vector=vector,
                payload=payload_with_text,
            )
        ],
    )

    info2 = client.get_collection(collection)
    print(f"  Upsert tamam. Point ID: {point_id}")
    print(f"  Collection nokta sayısı: {info2.points_count}")
    return point_id


def step2_run_pipeline() -> dict:
    """Test provizyonunu pipeline'dan geçirir (MedGemma kapalı, ödeme sinyali açık)."""

    _section("ADIM 2: Test provizyonunu pipeline'dan geçir")

    from provizyon_engine.models import ProvizyonJob
    from provizyon_engine.orchestrator import OrchestratorConfig, ProvizyonOrchestrator

    job = ProvizyonJob(**TEST_JOB_DICT)
    print(f"  Provizyon ID : {job.provizyon_id}")
    print(f"  Kurum        : {job.model_extra.get('institution_name')}")
    print(f"  Tanı         : {job.diagnoses}")
    print(f"  İşlem        : {job.procedures[0].code} {job.procedures[0].name}")
    print(f"  Ödeme kodu   : {job.procedures[0].model_extra.get('payment_procedure_code')}")

    config = OrchestratorConfig(
        enable_diagnosis=True,
        enable_sut_diagnosis=True,
        enable_sut_rules=True,
        enable_medgemma=False,
        enable_diagnosis_payment=True,
        enable_persistence=False,
        enable_patient_context=False,
        use_qdrant_rag=True,
    )
    orch = ProvizyonOrchestrator(config=config)
    t0 = time.time()
    result = orch.run(job)
    elapsed = round(time.time() - t0, 2)

    summary = {
        "provizyon_id": result.provizyon_id,
        "nihai_karar": result.nihai_karar.value,
        "decision_type": result.decision_type.value if result.decision_type else None,
        "risk_level": result.risk_level.value if result.risk_level else None,
        "gerekce": result.gerekce,
        "warnings": result.warnings,
        "risk_reasons": [
            {
                "layer": r.layer,
                "rule_trigger": r.rule_trigger,
                "risk_level": r.risk_level.value,
                "decision_type": r.decision_type.value,
                "message": r.message[:200],
            }
            for r in (result.risk_reasons or [])
        ],
        "diagnosis_payment_signals": result.raw.get("diagnosis_payment_signals"),
        "elapsed_sec": elapsed,
    }
    return summary


def step3_verify(summary: dict) -> bool:
    """Beklenen sonucu doğrular."""

    _section("ADIM 3: Sonuç doğrulama")

    karar = summary["nihai_karar"]
    risk_level = summary["risk_level"]
    reasons = summary.get("risk_reasons") or []
    has_payer_trend = any(
        r["layer"] == "diagnosis_payment_tendency" for r in reasons
    )

    checks = {
        "nihai_karar == manuel_inceleme": karar == "manuel_inceleme",
        "risk_level >= orange": risk_level in ("orange", "red"),
        "payer_payment_tendency reason var": has_payer_trend,
    }

    all_ok = True
    for label, ok in checks.items():
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {label}")
        if not ok:
            all_ok = False

    return all_ok


def main() -> int:
    print("SİNCAN KORU HASTANESİ + 06.42499 DERİ PRİCK TESTİ — Ödeme Eğilimi Demo")
    print(f"Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    point_id = step1_upsert_signal()

    summary = step2_run_pipeline()

    _section("PIPELINE SONUCU")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    ok = step3_verify(summary)

    _section("DEMO SONUÇ")
    if ok:
        print("  Tüm kontroller geçti. Ödeme eğilimi sinyali başarıyla çalışıyor.")
    else:
        print("  Bazı kontroller başarısız. Çıktıyı inceleyin.")

    out_path = Path(settings.GEMMA_ROOT / "logs" / "demo-sincan-koru-result.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  Sonuç kaydedildi: {out_path}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

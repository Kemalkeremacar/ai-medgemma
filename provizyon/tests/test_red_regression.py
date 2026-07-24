"""Kırmızı / savunulabilir risk regression testleri.

Sistemin açık red üretmesi gereken 5 senaryo:
  1. Tanı yok (işlem tanı gerektiriyor ama ICD-10 girilmemiş)
  2. Tanı uyumsuz (girilen ICD-10 işlemle uyumsuz)
  3. Yaş/cinsiyet şartı fail (MedGemma yas_cinsiyet_uygun=False)
  4. Zorunlu evrak yok (belge gerekli ama yüklenmemiş)
  5. Hasta-belge uyuşmazlığı (belge başka hastaya ait)

Her test ``merge_decisions`` fonksiyonunu çağırır ve ``decision_type`` / ``risk_level``
çıktısını doğrular. Dış servise ihtiyaç duymaz.

Çalıştırma:
    cd provizyon && .venv/bin/python -m pytest tests/test_red_regression.py -v
"""

from __future__ import annotations

import unittest

from provizyon_engine.decision import merge_decisions
from provizyon_engine.models import (
    DecisionType,
    KararDurumu,
    LayerResult,
    LayerStatus,
    MedGemmaClinicalOutput,
    RiskLevel,
)


def _mg_positive() -> MedGemmaClinicalOutput:
    return MedGemmaClinicalOutput(
        islem_belge_destekli=True,
        tani_belge_destekli=True,
        yas_cinsiyet_uygun=True,
        klinik_celiski=False,
        eksik_evrak=False,
        manuel_inceleme_gerekli=False,
        guven="high",
        gerekce="İşlem ve tanı belgelerle destekleniyor.",
    )


class RedRegressionTests(unittest.TestCase):
    """Sistemin kırmızı/savunulabilir risk üretmesi gereken senaryolar."""

    # ── 1. Tanı yok ─────────────────────────────────────────────────
    def test_missing_diagnosis_produces_red(self):
        """İşlem tanı gerektiriyor ama ICD-10 girilmemiş → TANI_EKSIK / RED."""

        tani = LayerResult(
            layer="tani_kurali",
            status=LayerStatus.FAIL,
            message="İşlem için gerekli tanı eksik.",
            detail={
                "missing_diagnosis": True,
                "result": {
                    "items": [
                        {
                            "huv_code": "24.73601",
                            "procedure_name": "BT toraks",
                            "status": "missing_diagnosis",
                            "input_diagnoses": [],
                        }
                    ]
                },
            },
        )
        outcome = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=tani,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=None,
            medgemma_layer=None,
        )
        self.assertEqual(outcome.karar, KararDurumu.TANI_EKSIK)
        self.assertEqual(outcome.decision_type, DecisionType.AUTOMATIC_DEFENSIBLE)
        self.assertEqual(outcome.risk_level, RiskLevel.RED)

    def test_missing_diagnosis_not_overridden_by_medgemma(self):
        """MedGemma olumlu olsa bile tanı eksikliği red kalmalı."""

        tani = LayerResult(
            layer="tani_kurali",
            status=LayerStatus.FAIL,
            message="İşlem için gerekli tanı eksik.",
            detail={
                "missing_diagnosis": True,
                "result": {
                    "items": [
                        {
                            "huv_code": "24.73601",
                            "procedure_name": "BT toraks",
                            "status": "missing_diagnosis",
                            "input_diagnoses": [],
                        }
                    ]
                },
            },
        )
        outcome = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=tani,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=_mg_positive(),
            medgemma_layer=None,
        )
        self.assertEqual(outcome.karar, KararDurumu.TANI_EKSIK)
        self.assertEqual(outcome.decision_type, DecisionType.AUTOMATIC_DEFENSIBLE)
        self.assertEqual(outcome.risk_level, RiskLevel.RED)

    # ── 2. Tanı uyumsuz ─────────────────────────────────────────────
    def test_diagnosis_mismatch_produces_red(self):
        """Girilen ICD-10 işlemle uyumsuz → TANI_UYUMSUZ / RED."""

        tani = LayerResult(
            layer="tani_kurali",
            status=LayerStatus.FAIL,
            message="Tanı işlemle uyumsuz.",
            detail={
                "diagnosis_mismatch": True,
                "result": {
                    "items": [
                        {
                            "huv_code": "24.73601",
                            "procedure_name": "BT toraks",
                            "status": "diagnosis_mismatch",
                            "input_diagnoses": ["Z00.0"],
                        }
                    ]
                },
            },
        )
        outcome = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=tani,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=None,
            medgemma_layer=None,
        )
        self.assertEqual(outcome.karar, KararDurumu.TANI_UYUMSUZ)
        self.assertEqual(outcome.decision_type, DecisionType.AUTOMATIC_DEFENSIBLE)
        self.assertEqual(outcome.risk_level, RiskLevel.RED)

    def test_sut_diagnosis_mismatch_produces_red(self):
        """SUT tanı kuralında uyumsuzluk da red üretmeli."""

        sut_tani = LayerResult(
            layer="sut_tani_kurali",
            status=LayerStatus.FAIL,
            message="SUT tanı uyumsuz.",
            detail={
                "diagnosis_mismatch": True,
                "result": {
                    "items": [
                        {
                            "sut_code": "530090",
                            "procedure_name": "Diyabet eğitimi",
                            "status": "diagnosis_mismatch",
                            "input_diagnoses": ["K35.0"],
                        }
                    ]
                },
            },
        )
        outcome = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=None,
            sut_tani_kurali=sut_tani,
            sut_kurali=None,
            medgemma=None,
            medgemma_layer=None,
        )
        self.assertEqual(outcome.karar, KararDurumu.TANI_UYUMSUZ)
        self.assertEqual(outcome.decision_type, DecisionType.AUTOMATIC_DEFENSIBLE)
        self.assertEqual(outcome.risk_level, RiskLevel.RED)

    # ── 3. Yaş/cinsiyet fail ────────────────────────────────────────
    def test_age_gender_fail_produces_red(self):
        """MedGemma yas_cinsiyet_uygun=False → KLINIK_UYUMSUZLUK / RED."""

        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=True,
            tani_belge_destekli=True,
            yas_cinsiyet_uygun=False,
            klinik_celiski=False,
            eksik_evrak=False,
            manuel_inceleme_gerekli=True,
            guven="high",
            gerekce="Hasta yaşı işlem için uygun değil.",
        )
        outcome = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=None,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=mg,
            medgemma_layer=None,
        )
        self.assertEqual(outcome.karar, KararDurumu.KLINIK_UYUMSUZLUK)
        self.assertEqual(outcome.decision_type, DecisionType.AUTOMATIC_DEFENSIBLE)
        self.assertEqual(outcome.risk_level, RiskLevel.RED)

    def test_clinical_conflict_produces_red(self):
        """MedGemma klinik_celiski=True → KLINIK_UYUMSUZLUK / RED."""

        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=True,
            tani_belge_destekli=True,
            yas_cinsiyet_uygun=True,
            klinik_celiski=True,
            eksik_evrak=False,
            manuel_inceleme_gerekli=True,
            guven="high",
            gerekce="Klinik çelişki tespit edildi.",
        )
        outcome = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=None,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=mg,
            medgemma_layer=None,
        )
        self.assertEqual(outcome.karar, KararDurumu.KLINIK_UYUMSUZLUK)
        self.assertEqual(outcome.decision_type, DecisionType.AUTOMATIC_DEFENSIBLE)
        self.assertEqual(outcome.risk_level, RiskLevel.RED)

    # ── 4. Zorunlu evrak yok ────────────────────────────────────────
    def test_missing_required_document_produces_yellow(self):
        """Belge gerekli ama yüklenmemiş → EVRAK_EKSIK / YELLOW."""

        zorunlu = LayerResult(
            layer="zorunlu_evrak",
            status=LayerStatus.FAIL,
            message="Gerekli evrak eksik: epikriz veya klinik rapor.",
        )
        outcome = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=zorunlu,
            tani_kurali=None,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=None,
            medgemma_layer=None,
        )
        self.assertEqual(outcome.karar, KararDurumu.EVRAK_EKSIK)
        self.assertEqual(outcome.decision_type, DecisionType.AUTOMATIC_DEFENSIBLE)
        self.assertEqual(outcome.risk_level, RiskLevel.YELLOW)

    def test_medgemma_missing_document_produces_red(self):
        """MedGemma eksik_evrak=True → EVRAK_EKSIK / RED (klinik hard negative)."""

        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=True,
            tani_belge_destekli=True,
            yas_cinsiyet_uygun=True,
            klinik_celiski=False,
            eksik_evrak=True,
            manuel_inceleme_gerekli=True,
            guven="medium",
            gerekce="Gerekli belge eksik.",
        )
        outcome = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=None,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=mg,
            medgemma_layer=None,
        )
        self.assertEqual(outcome.karar, KararDurumu.EVRAK_EKSIK)
        self.assertEqual(outcome.decision_type, DecisionType.AUTOMATIC_DEFENSIBLE)
        self.assertEqual(outcome.risk_level, RiskLevel.RED)

    # ── 5. Hasta-belge uyuşmazlığı ─────────────────────────────────
    def test_patient_document_mismatch_produces_red(self):
        """Belge başka hastaya ait → YANLIS_HASTA_BELGESI / RED."""

        belge_hasta = LayerResult(
            layer="belge_hasta",
            status=LayerStatus.FAIL,
            message="Belge beyan edilen hasta_id (12345) provizyon hastası (67890) ile uyuşmuyor.",
            detail={
                "documents": [
                    {
                        "title": "Epikriz",
                        "verdict": "mismatch",
                        "reason": "Belge beyan edilen hasta_id (12345) provizyon hastası (67890) ile uyuşmuyor.",
                    }
                ]
            },
        )
        outcome = merge_decisions(
            belge_hasta=belge_hasta,
            zorunlu_evrak=None,
            tani_kurali=None,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=None,
            medgemma_layer=None,
        )
        self.assertEqual(outcome.karar, KararDurumu.YANLIS_HASTA_BELGESI)
        self.assertEqual(outcome.decision_type, DecisionType.AUTOMATIC_DEFENSIBLE)
        self.assertEqual(outcome.risk_level, RiskLevel.RED)

    def test_patient_mismatch_overrides_everything(self):
        """Hasta-belge uyuşmazlığı, tanı uygun olsa bile red üretmeli (öncelik 1)."""

        belge_hasta = LayerResult(
            layer="belge_hasta",
            status=LayerStatus.FAIL,
            message="Belge başka bir hastaya ait.",
        )
        tani = LayerResult(
            layer="tani_kurali",
            status=LayerStatus.PASS,
            message="Tanı uyumlu.",
        )
        outcome = merge_decisions(
            belge_hasta=belge_hasta,
            zorunlu_evrak=None,
            tani_kurali=tani,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=_mg_positive(),
            medgemma_layer=None,
        )
        self.assertEqual(outcome.karar, KararDurumu.YANLIS_HASTA_BELGESI)
        self.assertEqual(outcome.decision_type, DecisionType.AUTOMATIC_DEFENSIBLE)
        self.assertEqual(outcome.risk_level, RiskLevel.RED)


class PriorityOrderTests(unittest.TestCase):
    """Karar öncelik sırasının korunduğunu doğrular."""

    def test_belge_hasta_fail_overrides_tani_fail(self):
        """Hasta-belge uyuşmazlığı (1) > tanı eksik (3) öncelik sırasında."""

        belge = LayerResult(layer="belge_hasta", status=LayerStatus.FAIL, message="Yanlış hasta.")
        tani = LayerResult(
            layer="tani_kurali",
            status=LayerStatus.FAIL,
            message="Tanı eksik.",
            detail={"missing_diagnosis": True, "result": {"items": [{"status": "missing_diagnosis", "input_diagnoses": []}]}},
        )
        outcome = merge_decisions(
            belge_hasta=belge,
            zorunlu_evrak=None,
            tani_kurali=tani,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=None,
            medgemma_layer=None,
        )
        self.assertEqual(outcome.karar, KararDurumu.YANLIS_HASTA_BELGESI)

    def test_evrak_eksik_overrides_tani_fail(self):
        """Zorunlu evrak eksik (2) > tanı uyumsuz (3) öncelik sırasında."""

        evrak = LayerResult(layer="zorunlu_evrak", status=LayerStatus.FAIL, message="Evrak eksik.")
        tani = LayerResult(
            layer="tani_kurali",
            status=LayerStatus.FAIL,
            message="Tanı uyumsuz.",
            detail={"diagnosis_mismatch": True, "result": {"items": [{"status": "diagnosis_mismatch", "input_diagnoses": ["Z00"]}]}},
        )
        outcome = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=evrak,
            tani_kurali=tani,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=None,
            medgemma_layer=None,
        )
        self.assertEqual(outcome.karar, KararDurumu.EVRAK_EKSIK)

    def test_all_pass_produces_green(self):
        """Tüm katmanlar uygun → UYGUN / GREEN (pozitif kontrol)."""

        belge = LayerResult(layer="belge_hasta", status=LayerStatus.PASS, message="Uyumlu.")
        evrak = LayerResult(layer="zorunlu_evrak", status=LayerStatus.PASS, message="Evrak tamam.")
        tani = LayerResult(layer="tani_kurali", status=LayerStatus.PASS, message="Tanı uyumlu.")
        outcome = merge_decisions(
            belge_hasta=belge,
            zorunlu_evrak=evrak,
            tani_kurali=tani,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=_mg_positive(),
            medgemma_layer=None,
        )
        self.assertEqual(outcome.karar, KararDurumu.UYGUN)
        self.assertEqual(outcome.decision_type, DecisionType.LOW_RISK)
        self.assertEqual(outcome.risk_level, RiskLevel.GREEN)


if __name__ == "__main__":
    unittest.main()

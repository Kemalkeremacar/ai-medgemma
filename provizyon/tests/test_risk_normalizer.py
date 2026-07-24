"""Provizyon risk normalizer unit testleri."""

from __future__ import annotations

import unittest

from provizyon_engine.models import (
    DecisionType,
    KararDurumu,
    LayerResult,
    LayerStatus,
    MedGemmaClinicalOutput,
    RiskLevel,
)
from provizyon_engine.risk_normalizer import (
    classify_diagnosis_item,
    medgemma_confidence_allows_diagnosis_override,
    medgemma_supports,
    normalize_provision_risk,
)


def _mg_supported() -> MedGemmaClinicalOutput:
    return MedGemmaClinicalOutput(
        islem_belge_destekli=True,
        tani_belge_destekli=True,
        yas_cinsiyet_uygun=True,
        guven="high",
        manuel_inceleme_gerekli=False,
        gerekce="Belgeler uyumlu.",
    )


def _mg_unsupported() -> MedGemmaClinicalOutput:
    return MedGemmaClinicalOutput(
        islem_belge_destekli=False,
        tani_belge_destekli=True,
        guven="medium",
        manuel_inceleme_gerekli=True,
    )


class ClassifyItemTests(unittest.TestCase):
    def test_missing_diagnosis_is_defensible(self):
        item = {
            "huv_code": "530090",
            "procedure_name": "Diyabet eğitimi",
            "status": "missing_diagnosis",
            "input_diagnoses": [],
        }
        reason = classify_diagnosis_item(item, layer_key="sut_tani_kurali", medgemma=None)
        self.assertEqual(reason.decision_type, DecisionType.AUTOMATIC_DEFENSIBLE)
        self.assertEqual(reason.risk_level, RiskLevel.RED)

    def test_review_required_supported_is_low_risk(self):
        item = {
            "huv_code": "24.73601",
            "procedure_name": "BT toraks",
            "status": "review_required",
            "requires_manual_review": True,
            "input_diagnoses": ["K22"],
        }
        reason = classify_diagnosis_item(
            item, layer_key="tani_kurali", medgemma=_mg_supported()
        )
        self.assertEqual(reason.decision_type, DecisionType.LOW_RISK)
        self.assertEqual(reason.risk_level, RiskLevel.GREEN)
        self.assertIn("Savunulabilir provizyon riski saptanmadı", reason.message)

    def test_review_required_unsupported_policy_stays_manual_with_medgemma(self):
        item = {
            "huv_code": "34.14421",
            "procedure_name": "Antijen arama",
            "status": "review_required",
            "requires_manual_review": True,
            "tentative_status": "unsupported_policy",
            "message": "Bu HUV işlem-tanı kuralı manuel/uzman incelemesi gerektiriyor; otomatik provizyon kararı verilmemeli.",
            "input_diagnoses": ["J11"],
        }
        reason = classify_diagnosis_item(
            item, layer_key="tani_kurali", medgemma=_mg_supported()
        )
        self.assertEqual(reason.decision_type, DecisionType.MANUAL_REVIEW)
        self.assertEqual(reason.risk_level, RiskLevel.ORANGE)
        self.assertIn("otomatik onay kapsamında değil", reason.message)

    def test_review_required_unsupported_is_manual(self):
        item = {
            "huv_code": "24.73601",
            "status": "review_required",
            "requires_manual_review": True,
            "input_diagnoses": ["K22"],
        }
        reason = classify_diagnosis_item(
            item, layer_key="tani_kurali", medgemma=_mg_unsupported()
        )
        self.assertEqual(reason.decision_type, DecisionType.MANUAL_REVIEW)
        self.assertEqual(reason.risk_level, RiskLevel.ORANGE)

    def test_allowed_no_diagnosis_is_low_risk(self):
        item = {
            "huv_code": "01.14285",
            "status": "allowed_no_diagnosis_required",
            "diagnosis_policy": "not_required",
            "input_diagnoses": ["J01"],
        }
        reason = classify_diagnosis_item(item, layer_key="tani_kurali", medgemma=None)
        self.assertEqual(reason.decision_type, DecisionType.LOW_RISK)
        self.assertEqual(reason.risk_level, RiskLevel.BLUE)

    def test_docless_medium_soft_review_is_low_risk(self):
        item = {
            "huv_code": "34.53153",
            "procedure_name": "Kan sayımı",
            "status": "review_required",
            "requires_manual_review": True,
            "tentative_status": "unsupported_policy",
            "diagnosis_required": False,
            "input_diagnoses": ["J06.9"],
        }
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=None,
            tani_belge_destekli=None,
            eksik_evrak=None,
            yas_cinsiyet_uygun=True,
            klinik_celiski=False,
            guven="medium",
            manuel_inceleme_gerekli=False,
            gerekce="Belgesiz klinik uyumlu.",
        )
        self.assertTrue(medgemma_confidence_allows_diagnosis_override(mg))
        reason = classify_diagnosis_item(item, layer_key="tani_kurali", medgemma=mg)
        self.assertEqual(reason.decision_type, DecisionType.LOW_RISK)
        self.assertEqual(reason.risk_level, RiskLevel.GREEN)
        self.assertIn("belgesiz", reason.message.lower())

    def test_belgeli_medium_soft_review_stays_manual(self):
        item = {
            "huv_code": "34.53153",
            "status": "review_required",
            "requires_manual_review": True,
            "diagnosis_required": False,
            "input_diagnoses": ["J06.9"],
        }
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=True,
            tani_belge_destekli=True,
            eksik_evrak=False,
            guven="medium",
            manuel_inceleme_gerekli=False,
        )
        self.assertFalse(medgemma_confidence_allows_diagnosis_override(mg))
        reason = classify_diagnosis_item(item, layer_key="tani_kurali", medgemma=mg)
        self.assertEqual(reason.decision_type, DecisionType.MANUAL_REVIEW)


class NormalizeProvisionRiskTests(unittest.TestCase):
    def _tani_review_layer(self) -> LayerResult:
        return LayerResult(
            layer="tani_kurali",
            status=LayerStatus.REVIEW,
            message="Tanı kuralları manuel inceleme gerektiriyor.",
            detail={
                "review_required": True,
                "result": {
                    "items": [
                        {
                            "huv_code": "24.73601",
                            "procedure_name": "BT toraks",
                            "status": "review_required",
                            "requires_manual_review": True,
                            "input_diagnoses": ["K22"],
                        }
                    ]
                },
            },
        )

    def test_review_only_with_medgemma_becomes_low_risk(self):
        out = normalize_provision_risk(
            karar=KararDurumu.UYGUN,
            gerekce="fallback",
            tani_kurali=self._tani_review_layer(),
            medgemma=_mg_supported(),
        )
        self.assertEqual(out.karar, KararDurumu.UYGUN)
        self.assertEqual(out.decision_type, DecisionType.LOW_RISK)
        self.assertEqual(out.risk_level, RiskLevel.GREEN)
        self.assertTrue(any(r.decision_type == DecisionType.LOW_RISK for r in out.risk_reasons))

    def test_tani_eksik_stays_defensible(self):
        layer = LayerResult(
            layer="tani_kurali",
            status=LayerStatus.FAIL,
            message="Tanı eksik",
            detail={
                "missing_diagnosis": True,
                "result": {
                    "items": [
                        {
                            "sut_code": "530090",
                            "procedure_name": "Diyabet eğitimi",
                            "status": "missing_diagnosis",
                            "input_diagnoses": [],
                        }
                    ]
                },
            },
        )
        out = normalize_provision_risk(
            karar=KararDurumu.TANI_EKSIK,
            gerekce="Tanı eksik",
            tani_kurali=layer,
        )
        self.assertEqual(out.karar, KararDurumu.TANI_EKSIK)
        self.assertEqual(out.decision_type, DecisionType.AUTOMATIC_DEFENSIBLE)
        self.assertEqual(out.risk_level, RiskLevel.RED)

    def test_medgemma_supports_helper(self):
        self.assertTrue(medgemma_supports(_mg_supported()))
        self.assertFalse(medgemma_supports(_mg_unsupported()))


    def test_risk_reasons_sorted_by_severity(self):
        """risk_reasons must be sorted: red/defensible first, then orange, then blue/green."""
        layer = LayerResult(
            layer="tani_kurali",
            status=LayerStatus.REVIEW,
            message="mixed",
            detail={
                "result": {
                    "items": [
                        {
                            "huv_code": "01.14285",
                            "status": "allowed_no_diagnosis_required",
                            "diagnosis_policy": "not_required",
                            "input_diagnoses": ["J01"],
                        },
                        {
                            "huv_code": "34.14421",
                            "procedure_name": "Antijen arama",
                            "status": "review_required",
                            "requires_manual_review": True,
                            "tentative_status": "unsupported_policy",
                            "message": "otomatik provizyon kararı verilmemeli.",
                            "input_diagnoses": ["J11"],
                        },
                        {
                            "huv_code": "530090",
                            "procedure_name": "Diyabet eğitimi",
                            "status": "missing_diagnosis",
                            "input_diagnoses": [],
                        },
                    ]
                }
            },
        )
        out = normalize_provision_risk(
            karar=KararDurumu.TANI_EKSIK,
            gerekce="test",
            tani_kurali=layer,
            medgemma=_mg_supported(),
        )
        levels = [(r.decision_type.value, r.risk_level.value) for r in out.risk_reasons]
        self.assertEqual(levels[0], ("automatic_defensible", "red"))
        self.assertEqual(levels[1], ("manual_review", "orange"))
        self.assertEqual(levels[2], ("low_risk", "blue"))


if __name__ == "__main__":
    unittest.main()

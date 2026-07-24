"""Belgesiz MedGemma prompt/post-process birim testleri."""

from __future__ import annotations

import unittest

from provizyon_engine.medgemma.clinical_eval import (
    DOCLESS_SYSTEM_PROMPT,
    _soften_docless_manual_flag,
)
from provizyon_engine.models import (
    JobResult,
    KararDurumu,
    LayerResult,
    LayerStatus,
    MedGemmaClinicalOutput,
)
from provizyon_engine.orchestrator import ProvizyonOrchestrator


class DoclessPromptTests(unittest.TestCase):
    def test_prompt_forbids_manual_for_rule_review_alone(self):
        self.assertIn("review_required", DOCLESS_SYSTEM_PROMPT)
        self.assertIn(
            "manuel_inceleme_gerekli=true yapmanı gerektirmez",
            DOCLESS_SYSTEM_PROMPT,
        )
        self.assertIn("soft_review=true", DOCLESS_SYSTEM_PROMPT)


class DeterministicSummaryTests(unittest.TestCase):
    def test_includes_compact_soft_review_items(self):
        orch = ProvizyonOrchestrator()
        result = JobResult(
            provizyon_id="test-summary",
            nihai_karar=KararDurumu.MANUEL_INCELEME,
            tani_kurali=LayerResult(
                layer="tani_kurali",
                status=LayerStatus.REVIEW,
                message="Tanı kuralları manuel inceleme gerektiriyor.",
                detail={
                    "overall_status": "review_required",
                    "blocking_items": [
                        {
                            "huv_code": "34.53153",
                            "procedure_name": "Kan sayımı (CBC)",
                            "status": "review_required",
                            "diagnosis_policy": "review_required",
                            "diagnosis_required": False,
                            "tentative_status": "unsupported_policy",
                            "reason": "Kan sayımı tanıdan bağımsız laboratuvar incelemesidir.",
                        }
                    ],
                },
            ),
            belge_hasta=LayerResult(
                layer="belge_hasta",
                status=LayerStatus.SKIPPED,
                message="Belgesiz akış",
            ),
        )
        summary = orch._deterministic_summary(result, docless=True)
        self.assertEqual(summary["documents_mode"], "skipped_full_pipeline")
        self.assertEqual(summary["belge_hasta"]["status"], "skipped")
        tani = summary["tani_kurali"]
        self.assertEqual(tani["status"], "review")
        self.assertEqual(tani["overall_status"], "review_required")
        self.assertEqual(len(tani["items"]), 1)
        item = tani["items"][0]
        self.assertEqual(item["code"], "34.53153")
        self.assertTrue(item["soft_review"])
        self.assertFalse(item["strict_manual"])
        self.assertIn("soft_review=true", summary["review_required_note"])


class SoftenDoclessManualFlagTests(unittest.TestCase):
    def test_softens_medium_without_clinical_conflict(self):
        parsed = MedGemmaClinicalOutput(
            islem_belge_destekli=None,
            tani_belge_destekli=None,
            eksik_evrak=None,
            guven="medium",
            manuel_inceleme_gerekli=True,
            klinik_celiski=False,
            yas_cinsiyet_uygun=True,
        )
        out, softened = _soften_docless_manual_flag(parsed)
        self.assertTrue(softened)
        self.assertFalse(out.manuel_inceleme_gerekli)

    def test_keeps_manual_on_clinical_conflict(self):
        parsed = MedGemmaClinicalOutput(
            guven="medium",
            manuel_inceleme_gerekli=True,
            klinik_celiski=True,
        )
        out, softened = _soften_docless_manual_flag(parsed)
        self.assertFalse(softened)
        self.assertTrue(out.manuel_inceleme_gerekli)

    def test_keeps_manual_on_low_confidence(self):
        parsed = MedGemmaClinicalOutput(
            guven="low",
            manuel_inceleme_gerekli=True,
            klinik_celiski=False,
        )
        out, softened = _soften_docless_manual_flag(parsed)
        self.assertFalse(softened)
        self.assertTrue(out.manuel_inceleme_gerekli)


if __name__ == "__main__":
    unittest.main()

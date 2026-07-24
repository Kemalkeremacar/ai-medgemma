"""DB intake kod sınıflandırma ve facility_level güvenliği."""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock

from provizyon_engine.intake.db_intake import _build_job_from_row, _infer_code_type, _parse_procedures
from provizyon_engine.models import ProcedureInput, ProvizyonJob


class InferCodeTypeTests(unittest.TestCase):
    def test_explicit_huv_sut(self):
        self.assertEqual(_infer_code_type("24.73601", "HUV"), "HUV")
        self.assertEqual(_infer_code_type("530140", "SUT"), "SUT")

    def test_empty_tip_uses_shape(self):
        self.assertEqual(_infer_code_type("24.73601", ""), "HUV")
        self.assertEqual(_infer_code_type("530140", ""), "SUT")

    def test_branch_codes_are_other(self):
        self.assertEqual(_infer_code_type("1700", ""), "other")
        self.assertEqual(_infer_code_type("2800", ""), "other")
        self.assertEqual(_infer_code_type("TZH.3.33.414", "HUV"), "other")
        self.assertEqual(_infer_code_type("TZH.3.33.414", ""), "other")


class ParseAndRouteTests(unittest.TestCase):
    def test_branch_only_not_huv_diagnosis(self):
        procs, fam = _parse_procedures("1700|Deri ve Zührevi Hastalıkları|")
        self.assertEqual(fam, None)
        self.assertEqual(procs[0].code_type, "other")
        job = ProvizyonJob(provizyon_id="P1", procedures=procs, code_family=fam)
        self.assertEqual(job.all_huv_codes(), [])
        self.assertEqual(job.diagnosis_code_source(), "none")

    def test_huv_and_branch_mixed(self):
        procs, fam = _parse_procedures(
            "24.73601|Test işlem|HUV<~>1700|Branş|"
        )
        self.assertEqual(fam, "HUV")
        job = ProvizyonJob(
            provizyon_id="P2",
            procedures=procs,
            code_family=fam,
            huv_codes=[p.code for p in procs if p.code_type == "HUV"],
        )
        self.assertEqual(job.all_huv_codes(), ["24.73601"])
        self.assertEqual(job.diagnosis_code_source(), "huv")

    def test_auto_shaped_huv_still_counts(self):
        job = ProvizyonJob(
            provizyon_id="P3",
            procedures=[ProcedureInput(code="01.14285", code_type="auto", name="x")],
        )
        self.assertEqual(job.all_huv_codes(), ["01.14285"])
        self.assertEqual(job.diagnosis_code_source(), "huv")


class BuildJobFacilityTests(unittest.TestCase):
    def test_kurum_tipi_not_facility_level(self):
        row = {
            "ProvizyonId": 1,
            "HastaAd": "A",
            "HastaSoyad": "B",
            "HastaYas": 40,
            "Cinsiyet": "Erkek",
            "TCKimlik": "12345678901",
            "UyeSicil": "0001",
            "UyeId": 1,
            "KurumAdi": "Test Hastane",
            "KurumTipi": "1 Nolu Gruptan Ayrılanlar (Katkı var)",
            "Brans": "Dahiliye",
            "DoktorAdi": "Dr",
            "IslemTipi": "AYAKTA TEDAVİ",
            "HizmetTarih": date(2026, 1, 15),
            "TaniBilgileri": "M54.8|Bel ağrısı",
            "IslemBilgileri": "24.73601|Test|HUV",
            "BelgeBilgileri": None,
        }
        job = _build_job_from_row(MagicMock(), row, skip_documents=True)
        self.assertIsNone(job.facility_level)
        self.assertTrue(any("KurumTipi(sigorta grubu)" in n for n in job.notes))
        self.assertEqual(job.procedures[0].date, "2026-01-15")
        self.assertEqual(job.documents_mode, "skipped_full_pipeline")


if __name__ == "__main__":
    unittest.main()

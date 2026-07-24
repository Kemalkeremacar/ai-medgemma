"""Popup (Hizmet Döküm Formu) ayrıştırıcı ve klasör-intake birim testleri.

Dış servise ihtiyaç duymaz; metin tabanlı parse'i sentetik metinle doğrular.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from provizyon_engine.intake.folder_intake import (
    _age_from_birthdate,
    build_job,
    collect_documents,
)
from provizyon_engine.documents.classify import gender_from_hizmet_alan
from provizyon_engine.intake.popup_parser import parse_popup_text
from provizyon_engine.models import Cinsiyet

SAMPLE = """T.C ZİRAAT BANKASI A.Ş. VE T. HALK BANKASI A.Ş.
HİZMET DÖKÜM FORMU
ANADOLU SAĞLIK MERKEZİ HASTANESİ
Üye Sicil No: 0030024
Provizyon No: 3208035
Üye Statü: Çalışan
Hizmet Alan: Kız Çocuk
Hasta Ad Soyad : MÜRÜVVET
KARADEMİRCİ
Provizyon Durum: Provizyon Değerlendirilmesi Tamamlandı.
TC Kimlik No 53491683054
Provizyon/Hizmet Zamanı: 23-01-2026 08:23
Doğum Tarihi 25-04-2010
Provizyon Detayları
Üst Tanı
ICD 10 Kod
Ad
Diğer
K22
Özofagusun diğer hastalıkları
Diğer
J01
Akut sinüzit
AYAKTA TEDAVİ
Sıra Hizmet
Kod
Ad
Toplam
Fatura Tutarı
Mı
1
24.73601
BİLGİSAYARLI
TORAKS TOMOGRAFİSİ
12.589,50
11.330,55
2
TZH.Ilac
İLAÇ
500,00
Toplam
13.089,50
saglik.tzhvakfi.org/PopupPage.aspx?typ=003&ID=3208035
"""


class PopupParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = parse_popup_text(SAMPLE)

    def test_header_fields(self) -> None:
        self.assertEqual(self.data.provizyon_no, "3208035")
        self.assertEqual(self.data.uye_sicil, "0030024")
        self.assertEqual(self.data.tc, "53491683054")
        self.assertEqual(self.data.dogum_tarihi, "25-04-2010")
        self.assertEqual(self.data.kurum, "ANADOLU SAĞLIK MERKEZİ HASTANESİ")

    def test_multiline_patient_name(self) -> None:
        self.assertEqual(self.data.hasta_ad, "MÜRÜVVET KARADEMİRCİ")

    def test_diagnoses(self) -> None:
        self.assertEqual(self.data.icd_codes, ["K22", "J01"])
        self.assertEqual(self.data.diagnoses[0].name, "Özofagusun diğer hastalıkları")

    def test_procedures_numeric_only_in_huv(self) -> None:
        codes = [p.code for p in self.data.procedures]
        self.assertIn("24.73601", codes)
        self.assertIn("TZH.Ilac", codes)
        # TZH kodları sayısal HUV listesine girmez.
        self.assertEqual(self.data.huv_codes, ["24.73601"])

    def test_procedure_name_captured(self) -> None:
        proc = next(p for p in self.data.procedures if p.code == "24.73601")
        self.assertEqual(proc.name, "BİLGİSAYARLI TORAKS TOMOGRAFİSİ")

    def test_no_warnings(self) -> None:
        self.assertEqual(self.data.warnings, [])


class IntakeHelperTests(unittest.TestCase):
    def test_age_from_birthdate_relative_to_provizyon(self) -> None:
        self.assertEqual(_age_from_birthdate("25-04-2010", "23-01-2026 08:23"), 15)
        self.assertEqual(_age_from_birthdate("25-04-2010", "25-04-2026 00:00"), 16)

    def test_gender_mapping(self) -> None:
        self.assertEqual(gender_from_hizmet_alan("Kız Çocuk"), Cinsiyet.KADIN)
        self.assertEqual(gender_from_hizmet_alan("Erkek Çocuk"), Cinsiyet.ERKEK)
        self.assertEqual(gender_from_hizmet_alan("Kendisi"), Cinsiyet.BILINMIYOR)

    def test_gender_from_epikriz_text(self) -> None:
        from provizyon_engine.documents.classify import infer_gender_from_text

        self.assertEqual(
            infer_gender_from_text("Cinsiyeti: Kadın\nHasta epikriz"),
            Cinsiyet.KADIN,
        )
        self.assertEqual(
            infer_gender_from_text("Cinsiyet: Erkek"),
            Cinsiyet.ERKEK,
        )
        self.assertEqual(
            infer_gender_from_text("Yaş / cinsiyeti :\n45 / ERKEK"),
            Cinsiyet.ERKEK,
        )

    def test_doc_type_from_text(self) -> None:
        from provizyon_engine.documents.classify import guess_doc_type_from_text

        self.assertEqual(guess_doc_type_from_text("LIV HOSPITAL EPİKRİZ"), "epikriz")
        self.assertEqual(guess_doc_type_from_text("ODYOMETRI FORMU"), "rapor")
        self.assertEqual(guess_doc_type_from_text("T.C. KİMLİK KARTI"), "kimlik")

    def test_build_job_and_documents(self) -> None:
        data = parse_popup_text(SAMPLE)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "epikriz.pdf").write_bytes(b"%PDF-1.4")
            (root / "rapor.txt").write_text("x")
            popup = root / "saglik_PopupPage_3208035.pdf"
            popup.write_bytes(b"%PDF-1.4")
            docs = collect_documents(root, popup)
            doc_names = sorted(Path(d.path).name for d in docs)
            self.assertEqual(doc_names, ["epikriz.pdf", "rapor.txt"])
            job = build_job(data, docs, fallback_id="X", cinsiyet=Cinsiyet.KADIN)
            self.assertEqual(job.provizyon_id, "3208035")
            self.assertEqual(job.patient_name, "MÜRÜVVET KARADEMİRCİ")
            self.assertEqual(job.tc_kimlik, "53491683054")
            self.assertEqual(job.cinsiyet, Cinsiyet.KADIN)
            self.assertEqual(job.huv_codes, ["24.73601"])
            self.assertEqual(job.diagnoses, ["K22", "J01"])


if __name__ == "__main__":
    unittest.main()

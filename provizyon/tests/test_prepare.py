"""MedGemma kanıt paketi / sayfa seçimi testleri."""

from __future__ import annotations

import unittest
from pathlib import Path

from provizyon_engine.documents.extract import ExtractedDocument, PageContent
from provizyon_engine.documents.prepare import build_evidence_package
from provizyon_engine.documents.source import DocumentRef


def _doc(title: str, pages: list[tuple[str, bool]], *, doc_type: str | None = None) -> ExtractedDocument:
    ref = DocumentRef(path=Path(f"/tmp/{title}.pdf"), title=title, doc_type=doc_type, exists=True)
    page_models = [
        PageContent(
            page_index=index,
            text=text,
            image_path=Path(f"/tmp/{title}_p{index}.png"),
            needs_ocr=needs_ocr,
        )
        for index, (text, needs_ocr) in enumerate(pages)
    ]
    return ExtractedDocument(ref=ref, pages=page_models)


class PrepareEvidenceTests(unittest.TestCase):
    def test_selects_first_and_last_page_per_document(self):
        long_doc = _doc(
            "uzun_rapor",
            [
                ("epikriz giris sayfasi hasta ERKAN", False),
                ("ara sayfa notu", False),
                ("ara sayfa iki", False),
                ("sonuc ve imza epikriz raporu", False),
            ],
            doc_type="epikriz",
        )
        package = build_evidence_package(
            [long_doc],
            max_images=2,
            huv_codes=["15.13077"],
            patient_name="Erkan Bektas",
        )
        self.assertEqual(package.selected_page_numbers, [1, 4])

    def test_per_document_quota_prefers_diverse_documents(self):
        doc_a = _doc("rapor_a", [("odyometri raporu HUV 16.006899", False)] * 6, doc_type="rapor")
        doc_b = _doc(
            "rapor_b",
            [
                ("form giris", False),
                ("ekokardiyografi sonuc raporu HUV 15.13077", False),
            ],
            doc_type="rapor",
        )
        package = build_evidence_package(
            [doc_a, doc_b],
            max_images=4,
            huv_codes=["15.13077", "16.006899"],
            procedure_names=["Ekokardiyografi", "Odyometri"],
        )
        self.assertEqual(len(package.selected_page_numbers), 4)
        self.assertIn(7, package.selected_page_numbers)  # doc_b last page (global idx)
        self.assertTrue(package.partial_vision)
        self.assertTrue(any("Gönderilmeyen" in note for note in package.notes))

    def test_missing_huv_keyword_boosts_matching_page(self):
        doc = _doc(
            "belge",
            [
                ("genel bilgi formu", False),
                ("muayene notu", False),
                ("islem kodu 24.00461 uygulandi", False),
            ],
        )
        package = build_evidence_package(
            [doc],
            max_images=2,
            extra_keywords=["24.00461"],
        )
        self.assertIn(3, package.selected_page_numbers)

    def test_text_evidence_orders_by_relevance(self):
        doc = _doc(
            "metin",
            [
                ("dusuk onemli not", False),
                ("epikriz raporu tani R00.2 ve odyometri", False),
            ],
        )
        package = build_evidence_package(
            [doc],
            max_text_chars=500,
            icd_codes=["R00.2"],
        )
        self.assertIn("odyometri", package.text_evidence.lower())
        self.assertLess(package.text_evidence.find("odyometri"), package.text_evidence.find("dusuk"))

    def test_unlimited_mode_sends_one_visual_per_page(self):
        doc = _doc(
            "tam",
            [("sayfa metni", False)] * 3,
        )
        for page in doc.pages:
            page.embedded_image_paths = [
                Path(f"/tmp/tam_p{page.page_index}_emb0.png"),
                Path(f"/tmp/tam_p{page.page_index}_emb1.png"),
            ]
        package = build_evidence_package([doc], max_images=0)
        # Sayfa başına tek görsel (embedded tercih; render+embedded çift sayımı yok)
        self.assertEqual(len(package.image_paths), 3)
        self.assertFalse(package.partial_vision)
        self.assertEqual(package.excluded_page_numbers, [])

    def test_low_ocr_quality_reduces_relevance(self):
        doc = _doc("dusuk", [("epikriz hasta tomografi", False)])
        doc.pages[0].ocr_quality = 0.2
        doc.pages[0].text = "epikriz hasta tomografi BT"
        package = build_evidence_package([doc], max_images=1, huv_codes=["24.73601"])
        self.assertEqual(len(package.selected_page_numbers), 1)


class ClinicalEvalQATests(unittest.TestCase):
    def test_fallback_qa_when_model_returns_empty(self):
        from provizyon_engine.medgemma.clinical_eval import _ensure_ozel_soru_cevaplari
        from provizyon_engine.models import MedGemmaClinicalOutput

        parsed = MedGemmaClinicalOutput(
            islem_belge_destekli=True,
            tani_belge_destekli=True,
            gerekce="Uygun.",
            guven="high",
        )
        out = _ensure_ozel_soru_cevaplari(parsed, ["Hasta yaşı nedir?"])
        self.assertTrue(out.ozel_soru_cevaplari)
        self.assertTrue(any(item.soru == "Hasta yaşı nedir?" for item in out.ozel_soru_cevaplari))

    def test_text_limit_scales_with_images(self):
        from provizyon_engine.medgemma.clinical_eval import _text_limit_for_vision

        self.assertLessEqual(_text_limit_for_vision(12), 6000)
        self.assertLessEqual(_text_limit_for_vision(8), 9000)
        self.assertGreater(_text_limit_for_vision(0), 9000)


if __name__ == "__main__":
    unittest.main()

"""OCR ve PDF extract iyileştirme testleri."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from provizyon_engine.documents.extract import (
    ExtractedDocument,
    PageContent,
    _is_garbled_text,
    _page_needs_ocr,
    extract_document,
)
from provizyon_engine.documents.classify import enrich_document_titles
from provizyon_engine.documents.source import DocumentRef


class GarbledDetectionTests(unittest.TestCase):
    def test_garbled_mixed_case(self) -> None:
        text = "KASRn NiANdll sAGLlt bEOZ yANmaya na asan vos ANYA"
        self.assertTrue(_is_garbled_text(text * 2))

    def test_clean_turkish_not_garbled(self) -> None:
        text = "RADYOLOJİ RAPORU Hasta Adı Soyadı Abdullah Akdemir bulgular normal"
        self.assertFalse(_is_garbled_text(text))

    def test_garbled_spaced_salad(self) -> None:
        text = "pe scs mm yy er m km mmm eee a i ee an ee ve a ee ttt po a ee m ye m m mm mm es"
        self.assertTrue(_is_garbled_text(text))

    def test_short_text_no_ocr(self) -> None:
        self.assertTrue(_page_needs_ocr(""))
        self.assertFalse(_page_needs_ocr("A" * 50))


class OCRQualityTests(unittest.TestCase):
    def test_quality_scores_clean_turkish_high(self) -> None:
        from provizyon_engine.documents.ocr import ocr_quality_score

        text = "EPİKRİZ RAPORU Hasta Adı Soyadı: Abdullah Akdemir Tanı: K22"
        self.assertGreaterEqual(ocr_quality_score(text), 0.7)

    def test_quality_scores_garbled_low(self) -> None:
        from provizyon_engine.documents.ocr import ocr_quality_score

        text = "KASRn NiANdll sAGLlt bEOZ yANmaya na asan vos ANYA " * 3
        self.assertLessEqual(ocr_quality_score(text), 0.2)

    def test_ocr_all_pages_skips_good_embedded_text(self) -> None:
        from provizyon_engine.documents.ocr import _should_ocr_page

        page = PageContent(
            page_index=0,
            text="RADYOLOJİ RAPORU " + ("hasta bulgular normal " * 5),
            image_path=Path("/tmp/p.png"),
            needs_ocr=False,
        )
        self.assertFalse(_should_ocr_page(page))


class DocumentTitleTests(unittest.TestCase):
    def test_enrich_title_from_heading(self) -> None:
        ref = DocumentRef(path=Path("/tmp/uuid.pdf"), doc_type="radyoloji_raporu", exists=True)
        doc = ExtractedDocument(
            ref=ref,
            pages=[PageContent(page_index=0, text="T.C. HASTANE RADYOLOJİ RAPORU\nHasta: Ali")],
        )
        enrich_document_titles([doc])
        self.assertIn("Radyoloji", doc.ref.title or "")
        self.assertIn("RADYOLOJİ", doc.ref.title or "")

    def test_garbled_line_not_used_as_title(self) -> None:
        ref = DocumentRef(path=Path("/tmp/uuid.pdf"), doc_type="rapor", exists=True)
        doc = ExtractedDocument(
            ref=ref,
            pages=[PageContent(
                page_index=0,
                text="| “| » j j |\nLİV HOSPİTAL ULUS ODYOMETRİ FORMU\nHasta: Test",
            )],
        )
        enrich_document_titles([doc])
        title = doc.ref.title or ""
        self.assertNotIn("|", title)
        self.assertIn("ODYOMETRİ", title)

    def test_no_heading_uses_label_only(self) -> None:
        ref = DocumentRef(path=Path("/tmp/uuid.pdf"), doc_type="rapor", exists=True)
        doc = ExtractedDocument(
            ref=ref,
            pages=[PageContent(page_index=0, text="e.\nC F\n” e")],
        )
        enrich_document_titles([doc])
        self.assertEqual(doc.ref.title, "Rapor")


class EmbeddedImageExtractTests(unittest.TestCase):
    def test_kimlik_pdf_extracts_text_after_ocr(self) -> None:
        intake = Path("/home/monassist1/GemmaApp/data/intake/3216442/3216442_20260303104840.PDF")
        if not intake.exists():
            self.skipTest("Intake dosyası yok")
        from provizyon_engine.documents.ocr import ocr_document

        ref = DocumentRef(path=intake, exists=True, kind="pdf")
        ext = extract_document(ref, render_images=True)
        self.assertGreater(ext.meta.get("embedded_images", 0), 0)
        ext = ocr_document(ext)
        text = ext.combined_text.lower()
        self.assertGreater(len(text), 200)
        self.assertIn("kimlik", text)


if __name__ == "__main__":
    unittest.main()

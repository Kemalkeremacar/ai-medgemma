"""Belge türü sınıflandırma birim testleri."""

from __future__ import annotations

import unittest

from provizyon_engine.documents.classify import (
    DocTypeGuess,
    classify_document,
    guess_doc_type_from_text,
    refine_doc_types,
)
from provizyon_engine.documents.extract import ExtractedDocument, PageContent
from provizyon_engine.documents.source import DocumentRef


def _doc(name: str, text: str, *, doc_type: str | None = None) -> ExtractedDocument:
    ref = DocumentRef(path=__import__("pathlib").Path(f"/tmp/{name}"), doc_type=doc_type, exists=True)
    return ExtractedDocument(
        ref=ref,
        pages=[PageContent(page_index=0, text=text, needs_ocr=len(text) < 40)],
    )


class ClassifyRulesTests(unittest.TestCase):
    def test_epikriz_high_confidence(self) -> None:
        guess = classify_document("belge.pdf", "LIV HOSPITAL EPİKRİZ RAPORU")
        self.assertIsNotNone(guess)
        assert guess is not None
        self.assertEqual(guess.doc_type, "epikriz")
        self.assertEqual(guess.confidence, "high")

    def test_kimlik_from_text(self) -> None:
        guess = classify_document("scan.pdf", "T.C. KİMLİK KARTI - NÜFUS CÜZDANI FOTOKOPİSİ")
        self.assertIsNotNone(guess)
        assert guess is not None
        self.assertEqual(guess.doc_type, "kimlik")
        self.assertEqual(guess.confidence, "high")

    def test_kimlik_card_ocr_garbled(self) -> None:
        text = "TURKIYE CUMHURIYETI KIMLIK KARTI REPUBLIC OF TURKEY IDENTITY CARD"
        guess = classify_document("scan.pdf", text)
        self.assertIsNotNone(guess)
        assert guess is not None
        self.assertEqual(guess.doc_type, "kimlik")

    def test_tc_kimlik_no_field_is_not_kimlik(self) -> None:
        text = "Hasta Ad Soyad: ALI VELI\nTC Kimlik No 12345678901\nUye Sicil No: 001"
        guess = classify_document("form.pdf", text)
        self.assertIsNotNone(guess)
        assert guess is not None
        self.assertNotEqual(guess.doc_type, "kimlik")
        guess = classify_document("3216442_kimlik.pdf", None)
        self.assertIsNotNone(guess)
        assert guess is not None
        self.assertEqual(guess.doc_type, "kimlik")
        self.assertEqual(guess.source, "filename")

    def test_radyoloji_from_procedure_hint(self) -> None:
        text = "Bilgisayarli toraks incelemesi yapilmistir. Bulgular normal."
        guess = classify_document(
            "uuid.pdf",
            text,
            procedure_names=["BİLGİSAYARLI TORAKS TOMOGRAFİSİ"],
        )
        self.assertIsNotNone(guess)
        assert guess is not None
        self.assertEqual(guess.doc_type, "radyoloji_raporu")
        self.assertEqual(guess.source, "procedure_hint")

    def test_generic_rapor_is_low_confidence(self) -> None:
        self.assertEqual(guess_doc_type_from_text("Bu bir genel rapor belgesidir."), "rapor")


class RefineDocTypesTests(unittest.TestCase):
    def test_skips_already_classified(self) -> None:
        doc = _doc("epikriz.pdf", "epikriz metni", doc_type="epikriz")
        doc.ref.meta["doc_type_confidence"] = "high"
        notes = refine_doc_types([doc])
        self.assertEqual(notes, [])

    def test_low_confidence_fallback_warns(self) -> None:
        doc = _doc("uuid.pdf", "kısa", doc_type=None)
        notes = refine_doc_types([doc])
        self.assertTrue(any("düşük güvenle" in n for n in notes))
        self.assertEqual(doc.ref.doc_type, "rapor")
        self.assertEqual(doc.ref.meta.get("doc_type_confidence"), "low")

    def test_high_confidence_classified_silently(self) -> None:
        doc = _doc("uuid.pdf", "RADYOLOJI RAPORU tomografi bulgulari", doc_type=None)
        notes = refine_doc_types([doc])
        self.assertEqual(notes, [])
        self.assertEqual(doc.ref.doc_type, "radyoloji_raporu")
        self.assertEqual(doc.ref.meta.get("doc_type_confidence"), "high")


if __name__ == "__main__":
    unittest.main()

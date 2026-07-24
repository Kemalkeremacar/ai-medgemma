"""Tests for prior rejection signal scanning."""

from __future__ import annotations

import unittest

from provizyon_engine.documents.rejection_signals import (
    scan_extracted_documents,
    scan_text_for_rejection_signals,
)


class RejectionSignalTests(unittest.TestCase):
    def test_detects_odenmez_phrase(self):
        text = (
            "Şikayetlerle uyumsuz vitaminler ve karotis vertebral doppler ODENMEZ. "
            "Faturanızı düzenleyiniz."
        )
        hits = scan_text_for_rejection_signals(text)
        self.assertTrue(hits)

    def test_detects_iade_nedeni(self):
        text = "Yapılan ek incelemede talep edilen tetkikler için belirtilen iade nedeni geçerlidir."
        hits = scan_text_for_rejection_signals(text)
        self.assertTrue(hits)

    def test_clean_text_no_hits(self):
        self.assertEqual(scan_text_for_rejection_signals("Epikriz ve lab sonuçları uyumlu."), [])

    def test_scan_extracted_documents(self):
        class Doc:
            full_text = "Dr. onayı: bu işlem ödenmez."

        hits = scan_extracted_documents([Doc()])
        self.assertEqual(len(hits), 1)


if __name__ == "__main__":
    unittest.main()

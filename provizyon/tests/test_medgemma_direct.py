"""Doğrudan MedGemma yardımcıları — dış servis gerektirmez."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from provizyon_engine.medgemma.direct import (
    collect_document_paths,
    materialize_images,
    run_direct_medgemma,
    DirectMedGemmaRequest,
)


class CollectPathsTests(unittest.TestCase):
    def test_collect_from_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.pdf").write_bytes(b"%PDF")
            (root / "b.jpg").write_bytes(b"\xff\xd8")
            (root / "skip.txt").write_text("x", encoding="utf-8")
            paths = collect_document_paths(folders=[root])
            names = {p.name for p in paths}
            self.assertEqual(names, {"a.pdf", "b.jpg"})


class MaterializeTests(unittest.TestCase):
    def test_image_copied_to_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "scan.jpg"
            src.write_bytes(b"\xff\xd8\xff")
            work = Path(tmp) / "work"
            work.mkdir()
            images, sources, truncation = materialize_images(
                [src], work, max_pages_per_pdf=0, max_images=5, dpi=150
            )
            self.assertEqual(len(images), 1)
            self.assertTrue(images[0].exists())
            self.assertEqual(sources, [str(src.resolve())])
            self.assertFalse(truncation["images_capped"])


class RunDirectTests(unittest.TestCase):
    @patch("provizyon_engine.medgemma.direct.MedGemmaVisionClient")
    def test_run_success_no_project_side_effects(self, client_cls: MagicMock):
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "x.png"
            img.write_bytes(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
            )
            mock_client = MagicMock()
            mock_client.ping.return_value = True
            mock_client.chat.return_value = "Özet: test yanıtı"
            mock_client.last_call_meta = None
            client_cls.return_value = mock_client

            result = run_direct_medgemma(
                DirectMedGemmaRequest(paths=[img], user_prompt="Özetle", max_images=4)
            )
            self.assertTrue(result.ok)
            self.assertIn("test yanıtı", result.response)
            mock_client.chat.assert_called_once()

    def test_empty_paths(self):
        result = run_direct_medgemma(DirectMedGemmaRequest(paths=[]))
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "Gönderilecek belge yok.")


if __name__ == "__main__":
    unittest.main()

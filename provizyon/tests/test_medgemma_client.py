"""MedGemma istemci fallback testleri."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from provizyon_engine.medgemma.client import (
    MedGemmaVisionClient,
    _image_tiers,
    _is_context_overflow,
    _is_transient_server_error,
)


class MedGemmaClientHelperTests(unittest.TestCase):
    def test_context_overflow_detection(self) -> None:
        exc = ValueError("Input length (36626) exceeds model's maximum context length (32768)")
        self.assertTrue(_is_context_overflow(exc))

    def test_transient_server_error_detection(self) -> None:
        exc = RuntimeError("Error code: 500 - Internal server error")
        self.assertTrue(_is_transient_server_error(exc))

    def test_image_tiers_descending(self) -> None:
        tiers = _image_tiers(16)
        self.assertEqual(tiers[0], 16)
        self.assertIn(0, tiers)


class MedGemmaClientFallbackTests(unittest.TestCase):
    def test_reduces_images_on_context_overflow(self) -> None:
        client = MedGemmaVisionClient.__new__(MedGemmaVisionClient)
        client.config = MagicMock()
        client.config.model = "test"
        client.config.temperature = 0.1
        client.config.max_tokens = 100
        client.config.vision_mode = "auto"
        client.client = MagicMock()

        overflow = ValueError("Input length (40000) exceeds model's maximum context length (32768)")
        ok_response = MagicMock()
        ok_response.choices = [MagicMock(message=MagicMock(content='{"gerekce":"ok"}'))]

        call_count = {"n": 0}

        def side_effect(**kwargs):
            call_count["n"] += 1
            content = kwargs["messages"][1]["content"]
            if isinstance(content, list) and len(content) > 5:
                raise overflow
            return ok_response

        client.client.chat.completions.create.side_effect = side_effect

        imgs = [Path(f"/tmp/fake_{i}.png") for i in range(8)]
        with patch.object(Path, "exists", return_value=True), patch(
            "provizyon_engine.medgemma.client._image_data_url",
            return_value="data:image/png;base64,abc",
        ):
            raw = client.chat("sys", "user text", image_paths=imgs, json_mode=True)

        self.assertIn("ok", raw)
        self.assertIsNotNone(client.last_call_meta)
        assert client.last_call_meta is not None
        self.assertLess(client.last_call_meta.vision_sent, 8)
        self.assertGreater(call_count["n"], 1)


if __name__ == "__main__":
    unittest.main()

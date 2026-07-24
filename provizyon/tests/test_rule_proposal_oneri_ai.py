"""Öneri AI — bağlam toplama, deterministik bölüm, fallback (MedGemma zorunlu değil)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from provizyon_engine.rule_proposal_oneri_ai import (
    build_deterministic_section,
    chat,
    gather_context,
)


class OneriAiTests(unittest.TestCase):
    def test_gather_context_returns_blocks(self):
        blocks, sources = gather_context("genel olarak neye bakmalıyım?")
        self.assertTrue(blocks)
        self.assertTrue(sources)

    def test_deterministic_section_from_proposal(self):
        blocks, _ = gather_context(
            "bu adayı özetle",
            proposal_id="engine_proposal_53cc05d9fb473883e988996d",
        )
        text = build_deterministic_section(blocks)
        self.assertIn("engine_proposal_53cc05d9fb473883e988996d", text)
        self.assertTrue(
            any(b.get("type") == "kural_onerisi" for b in blocks)
            or "Aday" in text
            or "Snapshot" in text
        )

    def test_chat_has_two_sections_without_medgemma(self):
        with patch(
            "provizyon_engine.rule_proposal_oneri_ai._call_medgemma_commentary",
            return_value=None,
        ):
            out = chat(
                "Bu adayı özetle",
                proposal_id="engine_proposal_53cc05d9fb473883e988996d",
            )
        self.assertFalse(out["usedModel"])
        self.assertIn("sections", out)
        self.assertTrue(out["sections"]["deterministic"])
        self.assertIsNone(out["sections"]["model"])
        self.assertIn("Deterministik öneriler", out["reply"])
        self.assertIn("Model yorumu", out["reply"])

    def test_chat_with_model_commentary(self):
        with patch(
            "provizyon_engine.rule_proposal_oneri_ai._call_medgemma_commentary",
            return_value="Evidence yeterli görünüyor; yaş sınırını ayrıca doğrulayın.",
        ):
            out = chat(
                "Onaylamalı mıyım?",
                proposal_id="engine_proposal_53cc05d9fb473883e988996d",
            )
        self.assertTrue(out["usedModel"])
        self.assertIn("Evidence yeterli", out["sections"]["model"] or "")
        self.assertIn("MedGemma", out["sources"])

    def test_chat_rejects_empty(self):
        with self.assertRaises(ValueError) as ctx:
            chat("   ")
        self.assertEqual(str(ctx.exception), "message_required")


if __name__ == "__main__":
    unittest.main()

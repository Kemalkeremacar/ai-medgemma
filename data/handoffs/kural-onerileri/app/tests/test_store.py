#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
HANDOFF_ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

from data_store import DataStore, _redact_secrets  # noqa: E402


class DataStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.store = DataStore(root=HANDOFF_ROOT, enable_raw=False)
        cls.store_raw = DataStore(root=HANDOFF_ROOT, enable_raw=True)

    def test_summary_counts(self) -> None:
        summary = self.store.get_summary()
        self.assertEqual(summary["counts"]["deterministicProposals"], 799)
        self.assertEqual(summary["base"]["deterministicProposals"], 799)
        self.assertEqual(summary["base"]["officialEvidence"], 1391)
        self.assertNotIn("completedPackets", summary["base"])
        self.assertEqual(summary["counts"]["completedPackets"], 3722)
        self.assertEqual(summary["counts"]["stage"]["rule_synthesis"], 799)
        self.assertEqual(summary["counts"]["stage"]["crosswalk_adjudication"], 1416)
        self.assertEqual(summary["counts"]["stage"]["proposal_rescue"], 1507)
        self.assertEqual(summary["counts"]["status"]["accepted"], 2828)
        self.assertEqual(summary["sourceState"], "complete")
        ai = summary["aiSnapshot"]
        self.assertEqual(ai["completedPackets"], 3722)
        self.assertEqual(ai["status"]["accepted"], 2828)
        self.assertEqual(ai["status"]["blocked"], 892)
        self.assertEqual(ai["status"]["call_or_parse_error"], 2)
        self.assertEqual(ai["stage"]["crosswalk_adjudication"], 1416)
        self.assertEqual(ai["stage"]["rule_synthesis"], 799)
        self.assertEqual(ai["stage"]["proposal_rescue"], 1507)
        self.assertEqual(ai["aiRuleHypotheses"], 637)
        self.assertEqual(ai["proposalRescueInsufficientEvidence"], 1507)
        self.assertEqual(ai["sourceState"], "complete")
        self.assertFalse(summary["safety"]["partialSnapshot"])
        self.assertFalse(summary["safety"]["rawEnabled"])

    def test_help_markdown(self) -> None:
        md = self.store.get_help_markdown()
        self.assertIn("Öneri", md)
        self.assertIn("Kanıt", md)
        self.assertIn("Durum", md)
        self.assertIn("HUV", md)
        self.assertIn("SUT", md)
        self.assertIn("tamamen ayrı", md)
        self.assertIn("birlikte ödenmez", md.lower())
        self.assertNotIn("Crosswalk", md)

    def test_proposal_display_title(self) -> None:
        item = self.store.proposal_list[0]
        self.assertIn("displayTitle", item)
        self.assertIn("shortTitle", item)
        self.assertNotIn("engine_proposal_", item["shortTitle"])
        self.assertTrue(item["shortTitle"].startswith(item["listeTipi"] or ""))
        detail = self.store.get_proposal(item["proposalId"])
        assert detail is not None
        self.assertEqual(detail["displayTitle"], item["displayTitle"])
        self.assertIn("completenessLabel", detail)

    def test_example_rules_sure_high(self) -> None:
        # First sure/complete with adet+periyot in sample set
        pid = "engine_proposal_53cc05d9fb473883e988996d"
        ex = self.store.get_example_rules(pid)
        self.assertIsNotNone(ex)
        assert ex is not None
        self.assertGreaterEqual(len(ex["examples"]), 1)
        self.assertIn("adet", ex["examples"][0]["text"].lower() + "3")
        self.assertIn("disclaimer", ex)
        self.assertIn(ex["consistency"]["level"], {"high", "medium", "low"})
        self.assertIn("3", ex["examples"][0]["text"])

    def test_example_rules_unknown_id(self) -> None:
        self.assertIsNone(self.store.get_example_rules("missing_id"))

    def test_proposal_pagination_and_filter(self) -> None:
        page1 = self.store.list_proposals(page=1, page_size=10)
        self.assertEqual(len(page1["items"]), 10)
        self.assertEqual(page1["total"], 799)
        sure = self.store.list_proposals(rule_type="sure", page=1, page_size=5)
        self.assertTrue(all(i["targetRuleType"] == "sure" for i in sure["items"]))
        prioritized = self.store.list_proposals(priority="A", page=1, page_size=50)
        self.assertTrue(all(i["priority"] == "A" for i in prioritized["items"]))
        self.assertGreater(prioritized["total"], 0)

    def test_proposal_detail_layers(self) -> None:
        first_id = self.store.proposal_list[0]["proposalId"]
        detail = self.store.get_proposal(first_id)
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertIn("proposal", detail)
        self.assertIn("officialEvidence", detail)
        self.assertIn("listeTipi", detail)
        self.assertNotIn("crosswalks", detail)
        self.assertNotIn("aiSyntheses", detail)
        self.assertNotIn("aiSyntheses", detail["proposal"])
        self.assertIn("aiHypotheses", detail)
        self.assertIn("labels", detail)
        self.assertIn("presentation", detail)
        self.assertIn("targetProcedures", detail["presentation"])
        self.assertIn("sourceSut", detail["presentation"])
        self.assertIn("mappingTrust", detail["presentation"])

    def test_rule_synthesis_on_proposals(self) -> None:
        # Final snapshot: every deterministic proposal has a rule_synthesis packet.
        with_ai = self.store.list_proposals(has_ai="1", page=1, page_size=5)
        self.assertEqual(with_ai["total"], 799)
        self.assertTrue(all(i.get("hasAiHypothesis") for i in with_ai["items"]))
        pid = with_ai["items"][0]["proposalId"]
        detail = self.store.get_proposal(pid)
        assert detail is not None
        self.assertGreaterEqual(len(detail["aiHypotheses"]), 1)
        hyp = detail["aiHypotheses"][0]
        self.assertEqual(hyp["stage"], "rule_synthesis")
        self.assertIn(hyp["status"], {"accepted", "blocked", "call_or_parse_error"})

    def test_ai_hypotheses_hide_machine_error_codes(self) -> None:
        blocked = self.store.list_ai(status="blocked", stage="rule_synthesis", page=1, page_size=20)
        self.assertGreater(blocked["total"], 0)
        # Find a proposal whose synthesis was blocked with machine codes in raw data.
        pid = None
        for row in blocked["items"]:
            owner = row.get("ownerId") or ""
            if owner.startswith("engine_proposal_"):
                pid = owner
                break
        if pid is None:
            # ownerId may be procedure key; scan proposals for blocked hyp
            for p in self.store.proposal_list[:50]:
                detail = self.store.get_proposal(p["proposalId"])
                assert detail is not None
                for h in detail["aiHypotheses"]:
                    if h.get("status") == "blocked" or h.get("validationNote"):
                        pid = p["proposalId"]
                        break
                if pid:
                    break
        self.assertIsNotNone(pid)
        detail = self.store.get_proposal(pid)
        assert detail is not None
        for h in detail["aiHypotheses"]:
            self.assertEqual(h.get("errors"), [])
            blob = " ".join(
                [
                    h.get("validationNote") or "",
                    h.get("rationale") or "",
                    h.get("statusLabel") or "",
                ]
            )
            self.assertNotIn("field_not_allowed", blob)
            self.assertNotIn("rule_synthesis_forbids", blob)
            if h.get("status") == "blocked":
                self.assertTrue(h.get("validationNote"))

    def test_ai_labels_and_status(self) -> None:
        accepted = self.store.list_ai(status="accepted", page=1, page_size=5)
        self.assertGreater(accepted["total"], 0)
        self.assertEqual(accepted["items"][0]["statusLabel"], "Teknik doğrulamayı geçti")
        blocked = self.store.list_ai(status="blocked", page=1, page_size=5)
        self.assertGreater(blocked["total"], 0)
        self.assertEqual(blocked["items"][0]["statusLabel"], "Güvenlik kontrolünde engellendi")
        blocked_id = blocked["items"][0]["packetId"]
        detail = self.store.get_ai(blocked_id)
        self.assertIsNotNone(detail)
        assert detail is not None
        # blocked + outcome yok iken "henüz işlenmedi" dememeli
        self.assertNotEqual(detail.get("outcomeLabel"), "Henüz işlenmedi")

    def test_proposal_rescue_not_counted_as_hypothesis(self) -> None:
        rescue = self.store.list_ai(stage="proposal_rescue", page=1, page_size=5)
        self.assertEqual(rescue["total"], 1507)
        self.assertTrue(all(i.get("stage") == "proposal_rescue" for i in rescue["items"]))
        # Rescue accepted packets are insufficient_evidence, not rule hypotheses.
        detail = self.store.get_ai(rescue["items"][0]["packetId"])
        assert detail is not None
        syn = (detail.get("result") or {}).get("synthesis") or {}
        self.assertEqual(syn.get("outcome"), "insufficient_evidence")
        self.assertEqual(detail.get("outcomeLabel"), "Kanıt yetersiz")

    def test_liste_tipi_filter(self) -> None:
        huv = self.store.list_proposals(liste_tipi="HUV", page=1, page_size=5)
        self.assertGreater(huv["total"], 0)
        self.assertTrue(all(i.get("listeTipi") == "HUV" for i in huv["items"]))

    def test_raw_disabled_by_default(self) -> None:
        packet = self.store.ai_results[0]["packetId"]
        self.assertIsNone(self.store.get_raw(packet))

    def test_raw_enabled(self) -> None:
        packet = self.store_raw.ai_results[0]["packetId"]
        raw = self.store_raw.get_raw(packet)
        self.assertIsNotNone(raw)
        assert raw is not None
        self.assertIn("DOĞRULANMAMIŞ", raw["warning"])
        self.assertIn("structured", raw)

    def test_redact_secrets(self) -> None:
        text = "Authorization: Bearer secret-token-123 api_key=abcd"
        redacted = _redact_secrets(text)
        self.assertNotIn("secret-token-123", redacted)
        self.assertIn("[REDACTED]", redacted)


if __name__ == "__main__":
    unittest.main()

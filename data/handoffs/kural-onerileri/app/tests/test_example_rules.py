#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
HANDOFF_ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

from data_store import DataStore  # noqa: E402
from example_rules import build_example_rules, resolve_birlikte_targets  # noqa: E402


class ExampleRulesUnitTests(unittest.TestCase):
    def test_huv_peers_same_list_rule(self) -> None:
        proposal = {
            "proposalId": "t_peers",
            "targetRuleType": "birlikteOdenmez",
            "priority": "A",
            "completeness": "complete",
            "qualityFlags": [],
            "officialEvidenceIds": ["e1"],
            "primaryProcedure": {
                "kod": "24.86366",
                "ad": "Prostat MRG",
                "listeTipi": "HUV",
            },
            "procedureRefs": [
                {"kod": "24.86366", "listeTipi": "HUV", "ad": "Prostat MRG"},
                {"kod": "24.80961", "listeTipi": "HUV", "ad": "Difüzyon MR"},
                {"kod": "24.92001", "listeTipi": "HUV", "ad": "MR Dinamik"},
            ],
            "proposedFields": {
                "evrakBazliMi": True,
                "targetSutCodes": ["SHOULD_NOT_APPEAR_AS_PEER"],
                "sourceSutCode": "G999",
            },
        }
        ex = build_example_rules(proposal, signals=[], evidence_count=1)
        text = ex["examples"][0]["text"]
        self.assertEqual(ex["examples"][0]["kind"], "birlikte")
        self.assertIn("HUV kodları 24.80961, 24.92001", text)
        self.assertIn("aynı sözleşme/listede", text)
        self.assertNotIn("SHOULD_NOT_APPEAR_AS_PEER", text)
        self.assertNotIn("Kaynak SUT", text)
        self.assertEqual(ex["usedFields"]["targetResolution"], "procedureRefs")
        self.assertTrue(ex["consistency"]["canApproveSameList"])

    def test_huv_with_only_sut_targets_blocked(self) -> None:
        proposal = {
            "proposalId": "t_blocked",
            "targetRuleType": "birlikteOdenmez",
            "priority": "B",
            "completeness": "partial",
            "qualityFlags": [],
            "officialEvidenceIds": ["e1"],
            "primaryProcedure": {"kod": "30.18511", "ad": "Kromozom", "listeTipi": "HUV"},
            "procedureRefs": [{"kod": "30.18511", "listeTipi": "HUV"}],
            "proposedFields": {
                "sourceSutCode": "G100050",
                "targetSutCodes": ["G100060"],
                "evrakBazliMi": True,
            },
        }
        ex = build_example_rules(proposal, signals=[], evidence_count=1)
        text = ex["examples"][0]["text"]
        self.assertEqual(ex["examples"][0]["kind"], "birlikte_cross_list_blocked")
        self.assertIn("onaylanamaz", text)
        self.assertIn("ayrı bir SUT kural adayı", text)
        self.assertNotIn("HUV kodları 30.19745", text)
        self.assertNotIn("SUT kodları G100060 ile", text)
        self.assertEqual(ex["usedFields"]["targetResolution"], "sut_targets_cross_list_blocked")
        self.assertFalse(ex["consistency"]["canApproveSameList"])
        self.assertEqual(ex["consistency"]["level"], "low")
        self.assertIn("cross_list_together_targets_blocked", ex["consistency"]["criticalFlags"])

    def test_sut_primary_uses_sut_targets(self) -> None:
        proposal = {
            "proposalId": "t_sut",
            "targetRuleType": "birlikteOdenmez",
            "priority": "B",
            "completeness": "complete",
            "qualityFlags": [],
            "officialEvidenceIds": ["e1"],
            "primaryProcedure": {"kod": "530150", "ad": "IV", "listeTipi": "SUT"},
            "procedureRefs": [{"kod": "530150", "listeTipi": "SUT"}],
            "proposedFields": {"targetSutCodes": ["530080", "530160"]},
        }
        ex = build_example_rules(proposal, signals=[], evidence_count=1)
        text = ex["examples"][0]["text"]
        self.assertIn("SUT kodları 530080, 530160", text)
        self.assertEqual(ex["usedFields"]["targetResolution"], "targetSutCodes")
        self.assertTrue(ex["consistency"]["canApproveSameList"])

    def test_sure_still_mentions_source_sut_as_note(self) -> None:
        proposal = {
            "proposalId": "t_sure",
            "targetRuleType": "sure",
            "priority": "A",
            "completeness": "complete",
            "qualityFlags": [],
            "officialEvidenceIds": ["e1"],
            "primaryProcedure": {"kod": "07.38065", "ad": "Test", "listeTipi": "HUV"},
            "proposedFields": {
                "adet": 3,
                "periyotDeger": 1,
                "surePeriyot": "G",
                "sourceSutCode": "530140",
                "islemlerGrupMu": False,
            },
        }
        ex = build_example_rules(proposal, signals=[], evidence_count=1)
        text = ex["examples"][0]["text"]
        self.assertIn("HUV 07.38065", text)
        self.assertIn("en fazla 3 adet", text)
        self.assertIn("Kaynak SUT (eşleme) referansı: 530140", text)

    def test_resolve_blocks_sut_only_on_huv(self) -> None:
        primary = {"kod": "1", "listeTipi": "HUV"}
        fields = {
            "procedureRefs": [{"kod": "1", "listeTipi": "HUV"}],
            "targetSutCodes": ["X"],
        }
        resolved = resolve_birlikte_targets(primary, fields)
        self.assertEqual(resolved["resolution"], "sut_targets_cross_list_blocked")
        self.assertEqual(resolved["targetCodes"], [])
        self.assertTrue(resolved["separateSutCandidate"])
        self.assertFalse(resolved["canApproveSameList"])

    def test_resolve_prefers_peers(self) -> None:
        primary = {"kod": "1", "listeTipi": "HUV"}
        fields = {
            "procedureRefs": [
                {"kod": "1", "listeTipi": "HUV"},
                {"kod": "2", "listeTipi": "HUV"},
            ],
            "targetSutCodes": ["X"],
        }
        resolved = resolve_birlikte_targets(primary, fields)
        self.assertEqual(resolved["resolution"], "procedureRefs")
        self.assertEqual(resolved["targetCodes"], ["2"])
        self.assertTrue(resolved["canApproveSameList"])


class ExampleRulesStoreIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.store = DataStore(root=HANDOFF_ROOT, enable_raw=False)

    def test_snapshot_huv_peers_example(self) -> None:
        pid = "engine_proposal_5f967774a1c69841b2bbf83e"
        ex = self.store.get_example_rules(pid)
        assert ex is not None
        text = ex["examples"][0]["text"]
        self.assertIn("HUV kodları", text)
        self.assertIn("24.80961", text)
        self.assertTrue(ex["consistency"]["canApproveSameList"])

    def test_snapshot_peer_case_approvable(self) -> None:
        pid = "engine_proposal_4458a4edca384ab45bfb34b2"
        ex = self.store.get_example_rules(pid)
        assert ex is not None
        text = ex["examples"][0]["text"]
        self.assertIn("30.19745", text)
        self.assertIn("HUV kodları", text)
        self.assertNotIn("SUT kodları G100060 ile", text)
        self.assertTrue(ex["consistency"]["canApproveSameList"])

    def test_snapshot_sut_only_on_huv_blocked(self) -> None:
        pid = "engine_proposal_0082445f976c54b4270938aa"
        ex = self.store.get_example_rules(pid)
        assert ex is not None
        self.assertEqual(ex["examples"][0]["kind"], "birlikte_cross_list_blocked")
        self.assertFalse(ex["consistency"]["canApproveSameList"])
        self.assertIn("onaylanamaz", ex["examples"][0]["text"])

    def test_proposal_detail_has_presentation(self) -> None:
        pid = "engine_proposal_4458a4edca384ab45bfb34b2"
        detail = self.store.get_proposal(pid)
        assert detail is not None
        pres = detail["presentation"]
        self.assertIn("targetProcedures", pres)
        self.assertTrue(pres["targetProcedures"]["canApproveSameList"])
        self.assertIn("30.19745", pres["targetProcedures"]["codes"])


if __name__ == "__main__":
    unittest.main()

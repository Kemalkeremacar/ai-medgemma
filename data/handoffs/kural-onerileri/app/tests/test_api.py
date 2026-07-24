#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
HANDOFF_ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

from data_store import DataStore  # noqa: E402
from server import DemoHandler  # noqa: E402
from http.server import ThreadingHTTPServer


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        store = DataStore(root=HANDOFF_ROOT, enable_raw=False)
        DemoHandler.store = store
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

        store_raw = DataStore(root=HANDOFF_ROOT, enable_raw=True)
        DemoHandlerRaw = type("DemoHandlerRaw", (DemoHandler,), {})
        DemoHandlerRaw.store = store_raw
        cls.server_raw = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandlerRaw)
        cls.port_raw = cls.server_raw.server_address[1]
        cls.thread_raw = threading.Thread(target=cls.server_raw.serve_forever, daemon=True)
        cls.thread_raw.start()
        cls.base_raw = f"http://127.0.0.1:{cls.port_raw}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_raw.shutdown()
        cls.server_raw.server_close()

    def _get(self, path: str, base: str | None = None) -> tuple[int, dict]:
        url = (base or self.base) + path
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return resp.status, body
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            return exc.code, body

    def test_index_html(self) -> None:
        with urllib.request.urlopen(self.base + "/", timeout=10) as resp:
            html = resp.read().decode("utf-8")
            self.assertEqual(resp.status, 200)
            self.assertIn("Kural Önerileri", html)

    def test_summary_api(self) -> None:
        status, body = self._get("/api/summary")
        self.assertEqual(status, 200)
        self.assertEqual(body["counts"]["deterministicProposals"], 799)

    def test_help_api(self) -> None:
        status, body = self._get("/api/help")
        self.assertEqual(status, 200)
        self.assertIn("markdown", body)
        self.assertIn("Öneri", body["markdown"])

    def test_example_rules_api(self) -> None:
        _, listing = self._get("/api/proposals?page=1&pageSize=1&priority=A")
        pid = listing["items"][0]["proposalId"]
        status, body = self._get(f"/api/proposals/{pid}/example-rules")
        self.assertEqual(status, 200)
        self.assertIn("examples", body)
        self.assertTrue(body["examples"])
        self.assertIn("taslak", body["disclaimer"].lower())

    def test_proposals_api(self) -> None:
        status, body = self._get("/api/proposals?page=1&pageSize=5&priority=A")
        self.assertEqual(status, 200)
        self.assertLessEqual(len(body["items"]), 5)
        self.assertTrue(all(i["priority"] == "A" for i in body["items"]))

    def test_proposal_detail_api(self) -> None:
        _, listing = self._get("/api/proposals?page=1&pageSize=1")
        pid = listing["items"][0]["proposalId"]
        status, body = self._get(f"/api/proposals/{pid}")
        self.assertEqual(status, 200)
        self.assertEqual(body["proposal"]["proposalId"], pid)
        self.assertIn("officialEvidence", body)

    def test_raw_forbidden_without_flag(self) -> None:
        _, listing = self._get("/api/ai?page=1&pageSize=1")
        packet = listing["items"][0]["packetId"]
        status, body = self._get(f"/api/raw/{packet}")
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "raw_disabled")

    def test_raw_allowed_with_flag(self) -> None:
        _, listing = self._get("/api/ai?page=1&pageSize=1", base=self.base_raw)
        packet = listing["items"][0]["packetId"]
        status, body = self._get(f"/api/raw/{packet}", base=self.base_raw)
        self.assertEqual(status, 200)
        self.assertIn("raw", body)
        self.assertIn("structured", body)

    def test_static_cannot_reach_restricted(self) -> None:
        # Static handler only serves STATIC_DIR; path traversal should 404.
        status, body = self._get("/../restricted/engine-proposals.ai-raw-responses.json")
        self.assertIn(status, (404, 403))


if __name__ == "__main__":
    unittest.main()

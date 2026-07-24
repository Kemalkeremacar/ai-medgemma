"""Demo fixture loader testleri."""

from __future__ import annotations

import unittest

from demo.fixture_loader import FIXTURES_ROOT, list_fixture_ids, load_job_from_fixture


class DemoFixtureLoaderTests(unittest.TestCase):
    def test_fixtures_exist(self):
        ids = list_fixture_ids()
        self.assertGreaterEqual(len(ids), 5)
        self.assertIn("demo-sut-530090-no-dx", ids)

    def test_load_resolves_document_paths(self):
        job = load_job_from_fixture("demo-sut-530090-e11")
        self.assertEqual(job.provizyon_id, "demo-sut-530090-e11")
        self.assertEqual(job.sut_codes, ["530090"])
        self.assertTrue(job.documents)
        self.assertTrue(job.documents[0].path.startswith(str(FIXTURES_ROOT)))


if __name__ == "__main__":
    unittest.main()

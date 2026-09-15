import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from desk import config as cfg
from desk.notion.client import FixtureNotionClient
from desk.service import DeskService
from desk.snapshot import load_fixture


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_refresh_without_a_credential_says_so_and_serves_the_fixture(self):
        with mock.patch.object(cfg, "NOTION_AUTH_PATH", Path(self.tmp.name) / "absent.json"), mock.patch.dict(os.environ):
            os.environ.pop(cfg.NOTION_MCP_TOKEN_ENV, None)
            service = DeskService(snapshot=load_fixture(), queue_path=None, snapshot_path=None, notion_client=None)
            result = service.refresh()
        self.assertEqual(result["kind"], "fixture")
        self.assertFalse(result["credential"])
        self.assertIn("python3 -m desk auth", result["note"])
        self.assertEqual(result["rooms"], 9)
        self.assertFalse(service.view()["write_path"]["credential"])

    def test_refresh_with_a_client_is_live_and_saved(self):
        snapshot_path = Path(self.tmp.name) / "snapshot.json"
        service = DeskService(snapshot=load_fixture(), queue_path=None, snapshot_path=snapshot_path, notion_client=FixtureNotionClient(load_fixture()))
        result = service.refresh()
        self.assertEqual(result["kind"], "live")
        self.assertTrue(result["credential"])
        self.assertEqual(result["saved_to"], str(snapshot_path))
        self.assertTrue(snapshot_path.exists())
        self.assertEqual(result["tasks"], 268)
        self.assertTrue(service.view()["write_path"]["credential"])


if __name__ == "__main__":
    unittest.main()

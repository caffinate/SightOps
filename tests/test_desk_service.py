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

    def test_two_processes_share_one_queue_file(self):
        """The server and the terminal commands keep the queue in the same file; each picks up what the other wrote."""
        queue_path = Path(self.tmp.name) / "queue.json"
        server = DeskService(snapshot=load_fixture(), queue_path=queue_path, snapshot_path=None, notion_client=FixtureNotionClient(load_fixture()))
        terminal = DeskService(snapshot=load_fixture(), queue_path=queue_path, snapshot_path=None, notion_client=FixtureNotionClient(load_fixture()))
        laptops = next(t for t in server.desk.tasks.values() if t.title == "Get laptops set up")
        proposal = terminal.change(laptops.id, "Notes", "Desk Harness decision proof", cfg.AUTHOR_AGENT, note="from the terminal")
        view = server.view()
        self.assertEqual(view["queue"]["summary"]["proposed"], 1)
        self.assertEqual(view["tasks"][laptops.id]["pending"][0]["id"], proposal.id)
        returned = server.respond(proposal.id, "Test of the join")
        self.assertEqual(returned.state, "returned")
        self.assertEqual(len(server.payload()), 1)
        item = next(i for i in terminal.desk.clipboard_items if i["state"] == "Open")
        terminal.rule(item["id"], "Dropped", "Dropped")
        calls = server.payload()
        self.assertEqual({c["tool"] for c in calls}, {"notion-create-pages", "notion-update-page"})
        self.assertEqual(server.desk.clipboard_by_id[item["id"]]["state"], "Dropped")
        # ten changes for the filed decision, three for the ruling; the returned proposal itself is settled
        self.assertEqual(terminal.view()["queue"]["summary"]["pending"], 13)

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

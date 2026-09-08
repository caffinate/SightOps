import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sightops.board import (
    build_board,
    column_for_status,
    native_status_for_move,
    patch_snapshot_status,
)
from sightops.server import canonical_request_path


class RequestPathTests(unittest.TestCase):
    def test_recovers_markdown_mangled_kanban_url(self):
        self.assertEqual(
            canonical_request_path("/ops-kanban.html](http://127.0.0.1:3000/ops-kanban.html)"),
            "/ops-kanban.html",
        )
        self.assertEqual(
            canonical_request_path("/ops-kanban.html%5D(http://127.0.0.1:3000/ops-kanban.html)"),
            "/ops-kanban.html",
        )

    def test_maps_favicon_to_icon(self):
        self.assertEqual(canonical_request_path("/favicon.ico"), "/dashboard-icon-192.png")
        self.assertEqual(canonical_request_path("/api/board"), "/api/board")


class ColumnMappingTests(unittest.TestCase):
    def test_maps_known_statuses(self):
        self.assertEqual(column_for_status("Not Started"), "ready")
        self.assertEqual(column_for_status("Not started"), "ready")
        self.assertEqual(column_for_status("Up next"), "ready")
        self.assertEqual(column_for_status("In Progress"), "doing")
        self.assertEqual(column_for_status("In progress"), "doing")
        self.assertEqual(column_for_status("To review"), "review")
        self.assertEqual(column_for_status("In review"), "review")
        self.assertEqual(column_for_status("Waiting"), "waiting")
        self.assertEqual(column_for_status(""), "inbox")
        self.assertEqual(column_for_status("Backlog"), "inbox")
        self.assertEqual(column_for_status("Done"), "done")
        self.assertEqual(column_for_status("Decided"), "done")
        self.assertEqual(column_for_status("Dropped"), "parked")

    def test_picks_native_status_from_same_database(self):
        observed = {
            "Team Thompson Tasks": ["Not Started", "In Progress", "Waiting"],
            "Website Tasks": ["Not started", "In progress"],
        }
        thompson = {"db_name": "Team Thompson Tasks", "status": "Not Started"}
        website = {"db_name": "Website Tasks", "status": "Not started"}
        self.assertEqual(native_status_for_move(thompson, "doing", observed), "In Progress")
        self.assertEqual(native_status_for_move(website, "doing", observed), "In progress")
        self.assertEqual(native_status_for_move(website, "done", observed), "Done")


class BoardBuildTests(unittest.TestCase):
    def test_attention_board_hides_idle_backlog(self):
        data = {
            "generated_at": "2026-08-29T22:00:00-07:00",
            "today": "2026-08-29",
            "overdue": [{
                "category": "PERSONAL",
                "db_name": "Team Thompson Tasks",
                "title": "Call dentist",
                "status": "Not Started",
                "due_date": "2026-08-01",
                "parsed_due": "2026-08-01",
                "priority": "High",
                "notes": "",
                "owner": "Nathan",
                "page_id": "task-overdue",
                "page_url": "https://app.notion.com/task-overdue",
            }],
            "due_now": [],
            "due_soon": [{
                "category": "SIGHTBOX",
                "db_name": "Website Tasks",
                "title": "Ship homepage",
                "status": "In progress",
                "due_date": "2026-09-01",
                "parsed_due": "2026-09-01",
                "priority": "Now",
                "notes": "Copy freeze",
                "owner": "Nathan",
                "page_id": "task-soon",
                "page_url": "https://app.notion.com/task-soon",
            }],
            "no_due": [
                {
                    "category": "SIGHTBOX",
                    "db_name": "Website Tasks",
                    "title": "Parked idea",
                    "status": "Backlog",
                    "due_date": "",
                    "parsed_due": None,
                    "priority": "",
                    "notes": "",
                    "owner": "",
                    "page_id": "task-idle",
                    "page_url": "https://app.notion.com/task-idle",
                },
                {
                    "category": "SIGHTBOX",
                    "db_name": "Website Tasks",
                    "title": "Waiting on legal",
                    "status": "Waiting",
                    "due_date": "",
                    "parsed_due": None,
                    "priority": "",
                    "notes": "",
                    "owner": "Charlie",
                    "page_id": "task-wait",
                    "page_url": "https://app.notion.com/task-wait",
                },
            ],
            "jobs": [],
            "total": 4,
        }
        board = build_board(data)
        self.assertEqual(board["needs_you"]["title"], "Call dentist")
        attention_ids = {card["page_id"] for card in board["cards"] if card["attention"]}
        self.assertEqual(attention_ids, {"task-overdue", "task-soon", "task-wait"})

    def test_patch_snapshot_updates_status_and_bucket(self):
        data = {
            "today": "2026-08-29",
            "overdue": [],
            "due_now": [],
            "due_soon": [{
                "page_id": "task-soon",
                "status": "Not started",
                "title": "Ship homepage",
                "parsed_due": "2026-09-01",
            }],
            "no_due": [],
        }
        updated = patch_snapshot_status(data, "task-soon", "Done")
        self.assertEqual(updated["due_soon"][0]["status"], "Done")


if __name__ == "__main__":
    unittest.main()

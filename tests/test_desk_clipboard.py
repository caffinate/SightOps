import tempfile
import unittest

from desk import clipboard as cb
from desk import config as cfg
from desk.model import Desk
from desk.notion.client import FixtureNotionClient
from desk.service import DeskService, fixture_service
from desk.snapshot import load_fixture


def task_named(desk, title):
    return next(t for t in desk.tasks.values() if t.title == title)


class ClipboardReadTests(unittest.TestCase):
    def test_fixture_carries_the_schema_with_the_task_pointer(self):
        snapshot = load_fixture()
        self.assertEqual(snapshot["clipboard"]["schema"][cb.TASK]["type"], "url")
        self.assertEqual(snapshot["clipboard"]["schema"][cb.ROOM]["target"], cfg.ROOM_REGISTER_ID)
        desk = Desk(snapshot)
        view = desk.clipboard_view()
        self.assertEqual(view["schema_source"], "snapshot")
        self.assertEqual((view["open"], view["adjudicated"], view["attached"], view["open_attached"]), (2, 12, 0, 0))
        self.assertEqual(len(view["items"]), 21)
        self.assertEqual(view["by_task"], {})
        self.assertNotIn("props", view["items"][0])
        self.assertIn("No Clipboard row points at a task yet", view["note"])

    def test_known_schema_is_the_fallback_for_an_older_snapshot(self):
        snapshot = load_fixture()
        snapshot["clipboard"].pop("schema")
        desk = Desk(snapshot)
        view = desk.clipboard_view()
        self.assertEqual(view["schema_source"], "known")
        self.assertIn("refresh the snapshot", view["note"])
        self.assertEqual(desk.schema_for_room(cfg.CLIPBOARD_ID)[cb.STATE]["options"], [cb.OPEN, cb.ADJUDICATED, cb.RETURNED, cb.DROPPED])

    def test_a_row_pointing_at_a_task_joins_it_and_a_stray_pointer_is_surfaced(self):
        snapshot = load_fixture()
        laptops = task_named(Desk(snapshot), "Get laptops set up")
        rows = snapshot["clipboard"]["rows"]
        rows[0][cb.TASK] = laptops.url
        rows[1][cb.TASK] = "https://www.notion.so/" + "f" * 32
        desk = Desk(snapshot)
        view = desk.clipboard_view()
        first, second = view["items"][0], view["items"][1]
        self.assertTrue(first["task_known"])
        self.assertEqual((first["task_id"], first["task_title"], first["task_room_id"]), (laptops.id, "Get laptops set up", laptops.room_id))
        self.assertEqual(view["by_task"], {laptops.id: [first["id"]]})
        self.assertEqual((view["attached"], view["open_attached"]), (1, 1))
        self.assertEqual(view["dangling"], [second["id"]])
        self.assertFalse(second["task_known"])
        full = desk.as_dict()
        self.assertEqual(full["tasks"][laptops.id]["decision_ids"], [first["id"]])
        self.assertIn(first["id"], full["rooms"][laptops.room_id]["clipboard_ids"])
        self.assertIn("1 of 21 rows point at a task", view["note"])


class ClipboardWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = fixture_service(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def open_item(self):
        return next(i for i in self.service.desk.clipboard_items if i["state"] == cb.OPEN)

    def test_a_ruling_queues_the_call_the_state_and_the_date_as_one_update_call(self):
        item = self.open_item()
        changes = self.service.rule(item["id"], "Leave it empty until the collector runs.", cb.ADJUDICATED)
        self.assertEqual([c.property for c in changes], [cb.CALL, cb.STATE, cb.RULED])
        self.assertTrue(all(c.state == "queued" and c.room_id == cfg.CLIPBOARD_ID and c.by == cfg.AUTHOR_PERSON for c in changes))
        calls = self.service.payload()
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual((call["tool"], call["room"], call["arguments"]["page_id"]), ("notion-update-page", cb.TITLE, item["id"]))
        props = call["arguments"]["properties"]
        self.assertEqual(props[cb.CALL], "Leave it empty until the collector runs.")
        self.assertEqual(props[cb.STATE], cb.ADJUDICATED)
        self.assertEqual(props["date:Ruled:start"], self.service.today())
        self.assertIsNone(props["date:Ruled:end"])
        local = self.service.desk.clipboard_by_id[item["id"]]
        self.assertEqual((local["state"], local["call"], local["ruled"]), (cb.ADJUDICATED, "Leave it empty until the collector runs.", self.service.today()))
        view = self.service.view()
        self.assertEqual(view["clipboard"]["open"], 1)
        self.assertEqual(len(view["clipboard"]["items"][0]["pending"]), 3)
        result = self.service.send()
        self.assertEqual(result["results"][0]["state"], "verified")
        self.assertEqual(self.service.queue.summary()["pending"], 0)

    def test_a_ruling_needs_a_call_and_a_known_state(self):
        item = self.open_item()
        with self.assertRaises(ValueError):
            self.service.rule(item["id"], "", cb.ADJUDICATED)
        with self.assertRaises(ValueError):
            self.service.rule(item["id"], "x", "Whatever")
        with self.assertRaises(KeyError):
            self.service.rule("0" * 32, "x")
        dropped = self.service.rule(item["id"], "", cb.DROPPED)
        self.assertEqual([c.property for c in dropped], [cb.STATE, cb.RULED])

    def test_accepting_a_proposal_files_a_decision_pointing_at_the_task(self):
        task = task_named(self.service.desk, "Get laptops set up")
        proposal = self.service.change(task.id, "Status", "In progress", cfg.AUTHOR_AGENT, note="laptops arrive Monday")
        accepted = self.service.accept(proposal.id)
        self.assertEqual(accepted.state, "queued")
        self.assertEqual(accepted.history[-1]["event"], "filed")
        filed = [c for c in self.service.queue.changes if c.room_id == cfg.CLIPBOARD_ID]
        row_id = accepted.history[-1]["clipboard_row"]
        self.assertTrue(filed)
        self.assertTrue(all(c.task_id == row_id and c.by == cfg.AUTHOR_PERSON and c.state == "queued" for c in filed))
        self.assertEqual({c.property for c in filed}, {cb.ITEM, cb.TYPE, cb.STATE, cb.ROOM, cb.TASK, cb.RAISED, cb.RULED, cb.OPTIONS_GIVEN, cb.CHOICES, cb.CALL})
        self.assertEqual([c.kind for c in filed].count("create"), 1)
        # locally the decision already shows on its task and its room
        view = self.service.view()
        self.assertEqual(view["tasks"][task.id]["decision_ids"], [row_id])
        self.assertIn(row_id, view["rooms"][task.room_id]["clipboard_ids"])
        item = view["clipboard"]["items"][-1]
        self.assertEqual((item["id"], item["state"], item["type"], item["call"]), (row_id, cb.ADJUDICATED, cb.DECISION, "Accepted."))
        self.assertEqual(item["room_names"], ["OLG Robotics"])
        self.assertEqual(item["item"], "Get laptops set up: Status → In progress")
        self.assertIn("because laptops arrive Monday", item["options_given"])
        self.assertTrue(item["task_known"])
        self.assertEqual(item["url"], f"local://{row_id}")
        # the payload: the task's update and one create call on the Clipboard, in its own vocabulary
        calls = self.service.payload()
        self.assertEqual([c["tool"] for c in calls], ["notion-update-page", "notion-create-pages"])
        create = calls[1]
        self.assertEqual(create["room"], cb.TITLE)
        self.assertEqual(create["arguments"]["parent"], {"type": "data_source_id", "data_source_id": cfg.CLIPBOARD_ID})
        props = create["arguments"]["pages"][0]["properties"]
        self.assertEqual(props[cb.TASK], task.url)
        self.assertEqual(props[cb.ROOM], [f"https://www.notion.so/{task.room_id}"])
        self.assertEqual((props[cb.STATE], props[cb.TYPE], props[cb.CALL]), (cb.ADJUDICATED, cb.DECISION, "Accepted."))
        self.assertEqual(props["date:Ruled:start"], self.service.today())
        self.assertEqual(props["date:Raised:start"], proposal.created_at[:10])
        self.assertIn("| REC", props[cb.CHOICES])
        # sent and read back, both calls
        result = self.service.send()
        self.assertEqual([r["state"] for r in result["results"]], ["verified", "verified"])
        self.assertTrue(result["results"][1]["created_page_id"])
        self.assertEqual(self.service.queue.summary()["pending"], 0)

    def test_reject_and_respond_file_their_ruling_and_ignore_does_not(self):
        task = task_named(self.service.desk, "Get laptops set up")
        p1 = self.service.change(task.id, "Status", "Done", cfg.AUTHOR_AGENT)
        self.service.reject(p1.id, "Not until the disks are made")
        p2 = self.service.change(task.id, "Status", "In progress", cfg.AUTHOR_AGENT)
        self.service.respond(p2.id, "Ask the room lead first")
        p3 = self.service.change(task.id, "Notes", "later", cfg.AUTHOR_AGENT)
        self.service.ignore(p3.id)
        calls = [i["call"] for i in self.service.desk.clipboard_items if i["task_id"] == task.id]
        self.assertEqual(calls, ["Rejected. Not until the disks are made", "Returned to the agent: Ask the room lead first"])
        self.assertEqual(len(self.service.view()["tasks"][task.id]["decision_ids"]), 2)
        self.assertEqual(self.service.queue.get(p3.id).history[-1]["event"], "ignored")

    def test_an_edited_acceptance_records_the_edit_in_the_call(self):
        task = task_named(self.service.desk, "Get laptops set up")
        p = self.service.change(task.id, "Status", "Done", cfg.AUTHOR_AGENT)
        self.service.accept(p.id, edited_to="In progress")
        item = self.service.desk.clipboard_items[-1]
        self.assertEqual(item["call"], "Accepted as edited: In progress.")
        self.assertEqual(self.service.desk.tasks[task.id].status_raw, "In progress")

    def test_filing_can_be_declined(self):
        task = task_named(self.service.desk, "Get laptops set up")
        p = self.service.change(task.id, "Status", "In progress", cfg.AUTHOR_AGENT)
        accepted = self.service.accept(p.id, file=False)
        self.assertEqual([c for c in self.service.queue.changes if c.room_id == cfg.CLIPBOARD_ID], [])
        self.assertEqual([c["tool"] for c in self.service.payload()], ["notion-update-page"])
        self.assertNotIn("filed", [h["event"] for h in accepted.history])

    def test_a_person_edits_a_clipboard_row_like_any_row(self):
        item = self.open_item()
        change = self.service.change(item["id"], cb.APPLIED_BY, "OLG Robotics", cfg.AUTHOR_PERSON)
        self.assertEqual((change.room_id, change.title), (cfg.CLIPBOARD_ID, item["item"]))
        self.assertEqual(self.service.desk.clipboard_by_id[item["id"]]["applied_by"], "OLG Robotics")
        with self.assertRaises(ValueError):
            self.service.change(item["id"], cb.STATE, "Maybe", cfg.AUTHOR_PERSON)
        with self.assertRaises(ValueError):
            self.service.change(item["id"], "Priority", "High", cfg.AUTHOR_PERSON)
        self.service.discard(change.id)
        self.assertIsNone(self.service.desk.clipboard_by_id[item["id"]]["applied_by"])

    def test_a_live_refresh_reads_the_clipboard_schema_and_title(self):
        service = DeskService(snapshot=load_fixture(), queue_path=None, snapshot_path=None, notion_client=FixtureNotionClient(load_fixture()))
        service.refresh()
        self.assertEqual(service.snapshot["clipboard"]["schema"][cb.TASK]["type"], "url")
        self.assertEqual(service.snapshot["clipboard"]["title"], cb.TITLE)
        self.assertEqual(service.desk.clipboard_view()["schema_source"], "snapshot")


if __name__ == "__main__":
    unittest.main()

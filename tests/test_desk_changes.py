import copy
import tempfile
import unittest
from pathlib import Path

from desk import config as cfg
from desk.changes import PendingQueue, Change
from desk.notion.client import FixtureNotionClient
from desk.service import DeskService, fixture_service
from desk.snapshot import load_fixture


def task_named(service, title):
    return next(t for t in service.desk.tasks.values() if t.title == title)


class WritePathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = fixture_service(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_person_change_applies_locally_and_queues(self):
        task = task_named(self.service, "Get laptops set up")
        change = self.service.change(task.id, "Status", "In progress", cfg.AUTHOR_PERSON)
        self.assertEqual(change.state, "queued")
        self.assertEqual(change.from_value, "Not started")
        self.assertEqual(self.service.desk.tasks[task.id].status_raw, "In progress")
        self.assertEqual(self.service.desk.tasks[task.id].state, "doing")
        view = self.service.view()
        self.assertEqual(len(view["tasks"][task.id]["pending"]), 1)
        self.assertEqual(view["queue"]["summary"], {"pending": 1, "queued": 1, "proposed": 0, "by_person": 1, "by_agent": 0})

    def test_payload_is_one_update_page_call_per_task_in_raw_vocabulary(self):
        task = task_named(self.service, "Get laptops set up")
        self.service.change(task.id, "Status", "In progress", cfg.AUTHOR_PERSON)
        self.service.change(task.id, "Due Date", "2026-09-26", cfg.AUTHOR_PERSON)
        self.service.change(task.id, "Owner", ["1cb13004-8266-40b6-92da-63771be9b248"], cfg.AUTHOR_PERSON)
        calls = self.service.payload()
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call["tool"], "notion-update-page")
        self.assertEqual(call["arguments"]["page_id"], task.id)
        self.assertEqual(call["arguments"]["command"], "update_properties")
        self.assertEqual(call["arguments"]["properties"], {
            "Status": "In progress",
            "date:Due Date:start": "2026-09-26", "date:Due Date:end": None, "date:Due Date:is_datetime": 0,
            "Owner": ["1cb13004-8266-40b6-92da-63771be9b248"],
        })
        self.assertEqual(call["room"], "OLG Robotics")

    def test_latest_change_per_property_wins(self):
        task = task_named(self.service, "Get laptops set up")
        self.service.change(task.id, "Status", "In progress", cfg.AUTHOR_PERSON)
        self.service.change(task.id, "Status", "Done", cfg.AUTHOR_PERSON)
        self.assertEqual(self.service.payload()[0]["arguments"]["properties"], {"Status": "Done"})

    def test_send_writes_and_reads_back(self):
        task = task_named(self.service, "Get laptops set up")
        self.service.change(task.id, "Status", "Done", cfg.AUTHOR_PERSON)
        result = self.service.send()
        self.assertTrue(result["sent"])
        self.assertEqual(result["results"][0]["state"], "verified")
        self.assertEqual(self.service.queue.summary()["pending"], 0)
        client = self.service.notion()
        self.assertEqual(client.calls[0]["properties"], {"Status": "Done"})
        self.assertEqual(client.page_properties(task.id)["Status"], "Done")

    def test_failed_send_stays_sendable(self):
        task = task_named(self.service, "Get laptops set up")
        self.service.change(task.id, "Status", "Done", cfg.AUTHOR_PERSON)
        client = FixtureNotionClient(self.service.snapshot, fail_on={task.id})
        result = self.service.send(client=client)
        self.assertEqual(result["results"][0]["state"], "failed")
        self.assertEqual(self.service.queue.summary()["queued"], 1)

    def test_readback_mismatch_is_a_failure(self):
        task = task_named(self.service, "Get laptops set up")
        change = self.service.change(task.id, "Status", "Done", cfg.AUTHOR_PERSON)

        class LyingClient(FixtureNotionClient):
            def page_properties(self, page_id):
                props = super().page_properties(page_id)
                props["Status"] = "Not started"
                return props

        result = self.service.send(client=LyingClient(copy.deepcopy(self.service.snapshot)))
        self.assertEqual(result["results"][0]["state"], "failed")
        self.assertEqual(result["results"][0]["mismatches"][0]["read_back"], "Not started")
        self.assertEqual(self.service.queue.get(change.id).state, "failed")

    def test_vocabulary_is_enforced_per_database(self):
        olg = task_named(self.service, "Get laptops set up")
        with self.assertRaises(ValueError):
            self.service.change(olg.id, "Status", "Not Started", cfg.AUTHOR_PERSON)  # Alkaline's capitalisation, not OLG's
        with self.assertRaises(ValueError):
            self.service.change(olg.id, "Status", "Not started", cfg.AUTHOR_PERSON)  # already the value
        with self.assertRaises(ValueError):
            self.service.change(olg.id, "Lane", "x", cfg.AUTHOR_PERSON)
        with self.assertRaises(KeyError):
            self.service.change("0" * 32, "Status", "Done", cfg.AUTHOR_PERSON)

    def test_discarding_a_person_change_reverts_the_local_copy(self):
        task = task_named(self.service, "Get laptops set up")
        change = self.service.change(task.id, "Status", "Done", cfg.AUTHOR_PERSON)
        self.service.discard(change.id)
        self.assertEqual(self.service.desk.tasks[task.id].status_raw, "Not started")
        self.assertEqual(self.service.payload(), [])

    def test_create_task_is_a_queued_change(self):
        room = next(r for r in self.service.desk.rooms if r.name == "Flux Films")
        change = self.service.create_task(room.id, "Cut the teaser to 30 seconds", cfg.AUTHOR_PERSON)
        self.assertEqual(change.kind, "create")
        self.assertIn(change.task_id, self.service.desk.tasks)
        self.assertEqual(self.service.desk.tasks[change.task_id].title, "Cut the teaser to 30 seconds")


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = fixture_service(self.tmp.name)
        self.task = task_named(self.service, "Draft light brand guidelines (logo, color, type)")

    def tearDown(self):
        self.tmp.cleanup()

    def test_agent_proposal_waits_at_the_gate(self):
        change = self.service.change(self.task.id, "Status", "In Progress", cfg.AUTHOR_AGENT, note="Kim started this on the 12th")
        self.assertEqual(change.state, "proposed")
        self.assertEqual(self.service.desk.tasks[self.task.id].status_raw, "Not Started")
        self.assertEqual(self.service.payload(), [])
        self.assertEqual(self.service.view()["queue"]["summary"]["proposed"], 1)

    def test_accept_enters_the_same_queue(self):
        change = self.service.change(self.task.id, "Status", "In Progress", cfg.AUTHOR_AGENT)
        accepted = self.service.accept(change.id)
        self.assertEqual(accepted.state, "queued")
        self.assertEqual(accepted.by, cfg.AUTHOR_AGENT)
        self.assertEqual(self.service.desk.tasks[self.task.id].status_raw, "In Progress")
        call = self.service.payload()[0]
        self.assertEqual(call["authors"], ["agent"])
        self.assertEqual(call["arguments"]["properties"], {"Status": "In Progress"})

    def test_accept_with_edits_records_the_edit(self):
        change = self.service.change(self.task.id, "Due Date", "2026-09-26", cfg.AUTHOR_AGENT)
        accepted = self.service.accept(change.id, edited_to="2026-09-30")
        self.assertTrue(accepted.edited)
        self.assertEqual(accepted.to_value, "2026-09-30")
        self.assertEqual(self.service.desk.tasks[self.task.id].due["start"], "2026-09-30")

    def test_reject_respond_and_ignore_are_distinct(self):
        c1 = self.service.change(self.task.id, "Status", "In Progress", cfg.AUTHOR_AGENT)
        c2 = self.service.change(self.task.id, "Priority", "High", cfg.AUTHOR_AGENT)
        c3 = self.service.change(self.task.id, "Notes", "x", cfg.AUTHOR_AGENT)
        self.assertEqual(self.service.reject(c1.id, "not yet").state, "rejected")
        self.assertEqual(self.service.respond(c2.id, "look at the whole project first").response, "look at the whole project first")
        self.assertEqual(self.service.ignore(c3.id).state, "ignored")
        self.assertEqual(self.service.payload(), [])
        with self.assertRaises(ValueError):
            self.service.accept(c1.id)

    def test_queue_persists_and_replays_onto_a_fresh_snapshot(self):
        change = self.service.change(self.task.id, "Status", "In Progress", cfg.AUTHOR_PERSON)
        proposal = self.service.change(self.task.id, "Priority", "High", cfg.AUTHOR_AGENT)
        reloaded = DeskService(snapshot=load_fixture(), queue_path=Path(self.tmp.name) / "queue.json", snapshot_path=None,
                               notion_client=FixtureNotionClient(load_fixture()))
        self.assertEqual(reloaded.desk.tasks[self.task.id].status_raw, "In Progress")
        self.assertEqual(reloaded.queue.get(change.id).state, "queued")
        self.assertEqual(reloaded.queue.get(proposal.id).state, "proposed")


class QueueUnitTests(unittest.TestCase):
    def test_change_new_sets_state_by_author(self):
        self.assertEqual(Change.new("t", "r", "Status", "A", "B", cfg.AUTHOR_PERSON).state, "queued")
        self.assertEqual(Change.new("t", "r", "Status", "A", "B", cfg.AUTHOR_AGENT).state, "proposed")

    def test_in_memory_queue_works_without_a_path(self):
        queue = PendingQueue(None)
        queue.add(Change.new("t", "r", "Status", "A", "B", cfg.AUTHOR_PERSON))
        self.assertEqual(len(queue.sendable()), 1)
        calls = queue.payload(lambda room_id: {"Status": {"type": "status", "options": ["A", "B"]}})
        self.assertEqual(calls[0]["arguments"]["properties"], {"Status": "B"})


if __name__ == "__main__":
    unittest.main()

import re
import unittest

from desk import config as cfg
from desk.model import Desk
from desk.normalise import normalise
from desk.registry import Room, format_vocabulary, parse_vocabulary, register_values_for, room_config
from desk.snapshot import load_fixture
from desk.tasks import counts

HEX32 = re.compile(r"[0-9a-f]{32}")

# The nine wired rooms as the brief lists them, with the grouping field each one uses.
BRIEF_GROUPING = {
    "Alkaline": "Category",
    "Beacon": "Phase",
    "Expert Practice": "Wave",
    "Flux Films": "Project",
    "OLG Robotics": "Category",
    "Sightbox 4.0 Business Development": "Build Area",
    "Sightbox 4.0 Transition Plan": "Workstream",
    "Upbound ModelPlane": "Project",
    "Vertical Vision GTM": "Time Box",
}


class FixtureDeskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.desk = Desk(load_fixture())

    def room(self, name):
        return next(r for r in self.desk.rooms if r.name == name)

    def findings(self, rule, room=None):
        return [f for f in self.desk.findings if f["rule"] == rule and (room is None or f["room"] == room)]

    def test_register_and_nine_wired_rooms(self):
        self.assertEqual(len(self.desk.rooms), 39)
        self.assertEqual(sorted(r.name for r in self.desk.rooms if r.wired), sorted(BRIEF_GROUPING))
        self.assertEqual(len(self.desk.tasks), 268)

    def test_grouping_fields_come_from_the_register_and_match_the_brief(self):
        for name, field in BRIEF_GROUPING.items():
            config = self.desk.configs[self.room(name).id]
            self.assertEqual(config.grouping_field, field, name)
            self.assertEqual(config.source, "register", name)
            self.assertEqual(config.warnings, [], name)

    def test_schema_guess_alone_would_also_match_the_brief(self):
        from desk.schema import guess_grouping_field

        for name, field in BRIEF_GROUPING.items():
            self.assertEqual(guess_grouping_field(self.desk.schema_for_room(self.room(name).id)), field, name)

    def test_owner_field_per_room(self):
        self.assertEqual(self.desk.configs[self.room("Upbound ModelPlane").id].owner_field, "Assignee")
        self.assertEqual(self.desk.configs[self.room("Sightbox 4.0 Business Development").id].owner_field, "Owners")
        self.assertIsNone(self.desk.configs[self.room("Beacon").id].owner_field)
        self.assertEqual(self.desk.configs[self.room("Alkaline").id].owner_field, "Owner")

    def test_status_vocabulary_is_each_databases_own(self):
        self.assertEqual(self.desk.configs[self.room("Beacon").id].status_vocabulary, ["Not started", "Up next", "In progress", "Blocked", "Done"])
        self.assertEqual(self.desk.configs[self.room("Alkaline").id].status_vocabulary, ["Not Started", "In Progress", "In Review", "Done"])
        self.assertEqual(self.desk.configs[self.room("Expert Practice").id].status_vocabulary, ["Not Started", "In Progress", "Blocked", "Done"])

    def test_counts_per_room(self):
        expected = {
            "Alkaline": {"rows": 50, "open": 35, "done": 15},
            "Beacon": {"rows": 21, "open": 19, "done": 2},
            "Sightbox 4.0 Business Development": {"rows": 63, "open": 44, "unset": 6, "done": 13},
            "Sightbox 4.0 Transition Plan": {"rows": 24, "open": 0, "unset": 3, "done": 21},
            "Upbound ModelPlane": {"rows": 41, "open": 10, "unset": 2, "done": 29},
            "Vertical Vision GTM": {"rows": 25, "open": 0, "done": 16, "cancelled": 9},
            "Flux Films": {"rows": 5, "open": 5},
            "OLG Robotics": {"rows": 8, "open": 5, "done": 3},
            "Expert Practice": {"rows": 31, "open": 4, "done": 27},
        }
        for name, want in expected.items():
            got = counts(self.desk.tasks_by_room[self.room(name).id])
            for key, value in want.items():
                self.assertEqual(got[key], value, f"{name} {key}")

    def test_alkaline_hierarchy_is_two_levels_deep(self):
        tasks = self.desk.tasks_by_room[self.room("Alkaline").id]
        stylescape = next(t for t in tasks.values() if t.title == "Evolve the chosen direction into Stylescape")
        concepts = next(t for t in tasks.values() if t.title == "Brand Concepts")
        self.assertEqual(stylescape.parent_id, concepts.id)
        self.assertEqual(len(stylescape.child_ids), 4)
        self.assertIn(stylescape.id, concepts.child_ids)
        tops = [t for t in tasks.values() if not t.parent_id]
        self.assertEqual(len(tops), 9)

    def test_raw_status_is_kept_and_state_is_derived(self):
        task = next(t for t in self.desk.tasks.values() if t.title == "Refine logo + build production lockups")
        self.assertEqual(task.status_raw, "Pending")
        self.assertEqual(task.state, "review")
        self.assertEqual(normalise("Canceled"), "cancelled")
        self.assertEqual(normalise(None), "unset")
        self.assertEqual(normalise("Up next"), "todo")
        self.assertEqual(normalise("Something odd", {"in_progress": ["Something odd"]}), "doing")
        self.assertEqual(normalise("Something odd"), "unknown")

    def test_owners_are_names_or_marked_unresolved_never_raw_ids(self):
        for task in self.desk.tasks.values():
            for name in task.owner_names:
                self.assertFalse(HEX32.search(name), name)
        self.assertEqual(set(self.desk.people.unresolved), {"70c0892ac3fd4e0b96f585cfb26c3cce", "6bc4c1587a184684aa6ba8446f2692d8"})
        task = next(t for t in self.desk.tasks.values() if t.title == "Visual Identity")
        self.assertEqual(task.owner_names, ["unresolved user 6bc4c158"])

    def test_known_dirt_is_surfaced(self):
        parents = self.findings("parent-closed-children-open", "Alkaline")
        self.assertEqual(len(parents), 1)
        self.assertEqual(parents[0]["count"], 5)
        self.assertEqual({i["title"] for i in parents[0]["items"]},
                         {"Verbal Identity", "Visual Identity", "Brand Guide", "Information Architecture", "Experience Design"})
        self.assertEqual(sum(f["count"] for f in self.findings("unresolved-owner")), 29)
        self.assertEqual(self.findings("empty-title", "Upbound ModelPlane")[0]["count"], 2)
        self.assertEqual({(f["room"], f["count"]) for f in self.findings("no-status")},
                         {("Sightbox 4.0 Business Development", 6), ("Sightbox 4.0 Transition Plan", 3), ("Upbound ModelPlane", 2)})
        self.assertEqual(len(self.findings("live-but-all-terminal", "Vertical Vision GTM")), 1)
        self.assertEqual(len(self.findings("one-due-date-for-all", "Vertical Vision GTM")), 1)
        foreign = self.findings("names-another-room")
        self.assertEqual([(f["room"], f["items"][0]["title"]) for f in foreign],
                         [("Sightbox 4.0 Business Development", "Alkaline website and branding update")])
        shapes = self.findings("outside-grouping", "Sightbox 4.0 Transition Plan")
        self.assertEqual(shapes[0]["count"], 6)
        self.assertTrue(any("Stream=Canon" in i["detail"] for i in shapes[0]["items"]))

    def test_nothing_is_cleaned(self):
        upbound = self.desk.tasks_by_room[self.room("Upbound ModelPlane").id]
        self.assertEqual(sum(1 for t in upbound.values() if t.title is None), 2)
        transition = self.desk.tasks_by_room[self.room("Sightbox 4.0 Transition Plan").id]
        self.assertEqual(sum(1 for t in transition.values() if t.status_raw is None), 3)

    def test_view_is_grouped_by_area_and_by_grouping_field(self):
        view = self.desk.as_dict()
        self.assertEqual([a["name"] for a in view["areas"]], ["Personal", "Sightbox", "Flux", "Other"])
        alkaline = view["rooms"][self.room("Alkaline").id]
        self.assertEqual([(g["label"], len(g["task_ids"])) for g in alkaline["groups"]],
                         [("Brand Identity", 5), ("Brand Experience", 3), ("No Category", 1)])
        beacon = view["rooms"][self.room("Beacon").id]
        self.assertEqual([g["label"] for g in beacon["groups"]], ["Phase 1", "Phase 2", "Phase 3", "Backlog"])
        self.assertEqual(view["clipboard"]["open"], 2)


class RegisterConfigTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = load_fixture()
        self.schema = self.snapshot["sources"]["b1e952c6-c7bb-4099-91a3-4a1a21c04512"]["schema"]

    def room(self, **overrides):
        base = dict(id="r1", name="Alkaline", area="Sightbox", status="Unknown", contract=None, tasks_url=None,
                    tasks_source="b1e952c6-c7bb-4099-91a3-4a1a21c04512", notion_home=None, clipboard=None,
                    clipboard_standard=None, notes=None)
        base.update(overrides)
        return Room(**base)

    def test_register_values_match_schema_when_populated(self):
        values = register_values_for(self.schema)
        self.assertEqual(values, {cfg.REGISTER_GROUPING_FIELD: "Category",
                                  cfg.REGISTER_STATUS_VOCABULARY: "Not Started, In Progress, In Review, Done",
                                  cfg.REGISTER_OWNER_FIELD: "Owner"})
        config = room_config(self.room(grouping_field="Category", status_vocabulary=parse_vocabulary(values[cfg.REGISTER_STATUS_VOCABULARY]), owner_field="Owner"), self.schema)
        self.assertEqual(config.source, "register")
        self.assertEqual(config.warnings, [])

    def test_drift_between_register_and_schema_is_surfaced_not_hidden(self):
        config = room_config(self.room(grouping_field="Category", status_vocabulary=["Not Started", "In Progress", "Done"], owner_field="Owner"), self.schema)
        self.assertEqual(config.source, "register")
        self.assertEqual(len(config.warnings), 1)
        self.assertIn("In Review", config.warnings[0])
        self.assertEqual(config.status_vocabulary, ["Not Started", "In Progress", "In Review", "Done"])

    def test_missing_property_named_on_register_falls_back(self):
        config = room_config(self.room(grouping_field="Lane", status_vocabulary=None, owner_field="Driver"), self.schema)
        self.assertEqual(config.grouping_field, "Category")
        self.assertEqual(config.owner_field, "Owner")
        self.assertEqual(config.source, "register+derived")
        self.assertEqual(len(config.warnings), 3)

    def test_vocabulary_text_round_trips(self):
        self.assertEqual(parse_vocabulary("A, B , C"), ["A", "B", "C"])
        self.assertEqual(parse_vocabulary("A, b | C"), ["A, b", "C"])
        self.assertEqual(format_vocabulary(["Not started", "Done"]), "Not started, Done")
        self.assertIsNone(parse_vocabulary("  "))


if __name__ == "__main__":
    unittest.main()
